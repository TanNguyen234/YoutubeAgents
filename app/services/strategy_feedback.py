"""Stage 15 Strategy Feedback Loop analyzing performance metrics and feeding insights into topic selection."""

from typing import Any, Dict, List, Optional
from app.db.repository import SQLiteRepository
from app.domain.models import AnalyticsSnapshot, VideoProject


class StrategyFeedbackLoop:
    """Analyzes published video performance and generates strategic adjustments for future content cycles."""

    def __init__(self, repository: SQLiteRepository):
        self.repo = repository

    def analyze_channel_performance(self, channel_id: str) -> Dict[str, Any]:
        """Compute performance baselines and identify top-performing content themes for a channel."""
        snapshots = self.repo.get_channel_analytics(channel_id)
        if not snapshots:
            return {
                "channel_id": channel_id,
                "total_snapshots": 0,
                "has_data": False,
                "mean_views": 0.0,
                "mean_watch_time_hours": 0.0,
                "mean_ctr_percent": 0.0,
                "mean_retention_3s": 0.0,
                "top_themes": [],
                "recommendations": ["No published performance data available yet. Rely on organic search demand."],
            }

        total_views = sum(s.views for s in snapshots)
        total_watch_time = sum(s.watch_time_hours for s in snapshots)
        total_ctr = sum(s.ctr_percent for s in snapshots)
        retention_values = [s.retention_at_3s_percent for s in snapshots if s.retention_at_3s_percent is not None]

        n = len(snapshots)
        mean_views = total_views / n
        mean_watch_time = total_watch_time / n
        mean_ctr = total_ctr / n
        mean_ret = (sum(retention_values) / len(retention_values)) if retention_values else 50.0

        # Discover top performing projects
        top_projects = []
        for s in snapshots:
            if s.views >= mean_views and s.ctr_percent >= mean_ctr:
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
        if mean_ret < 50.0:
            recommendations.append("Initial 3-second retention is below 50%. Strengthen opening script hooks and visual contrast.")
        else:
            recommendations.append(f"Strong opening hook retention ({mean_ret:.1f}%). Maintain current hook pacing.")

        if mean_ctr < 5.0:
            recommendations.append("Click-through rate is under 5.0%. Test punchier titles with technical curiosity gaps.")
        else:
            recommendations.append(f"Healthy CTR ({mean_ctr:.1f}%). Continue with technical architecture titles.")

        if top_projects:
            top_titles = ", ".join(f"'{p['title']}'" for p in top_projects[:2])
            recommendations.append(f"Prioritize topics similar to top performers: {top_titles}.")

        return {
            "channel_id": channel_id,
            "total_snapshots": n,
            "has_data": True,
            "mean_views": round(mean_views, 2),
            "mean_watch_time_hours": round(mean_watch_time, 2),
            "mean_ctr_percent": round(mean_ctr, 2),
            "mean_retention_3s": round(mean_ret, 2),
            "top_projects": top_projects,
            "recommendations": recommendations,
        }

    def compute_historical_fit_score(self, keyword: str, channel_id: str) -> float:
        """Calculate historical performance fit score (0.0 - 10.0) for candidate keyword."""
        analysis = self.analyze_channel_performance(channel_id)
        if not analysis.get("has_data") or not analysis.get("top_projects"):
            return 6.0  # Neutral baseline when history is absent

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
