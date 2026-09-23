"""Advanced SEO and Multi-Variant Packaging Service."""

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional
import uuid

from app.db.repository import SQLiteRepository
from app.domain.enums import TitleVariantType
from app.domain.models import Chapter, SEOPackage, TitleVariant, VideoProject


class SEOOptimizerService:
    """Generates multi-variant CTR titles, automated chapters, clustered tags, and conversion descriptions."""

    def __init__(self, repository: SQLiteRepository):
        self.repository = repository

    def generate_and_save_seo_package(
        self,
        project_id: str,
        primary_keyword: str,
        series_context: Optional[Dict[str, Any]] = None,
        sources_summary: Optional[str] = None,
        selected_title: Optional[str] = None,
        title_variants: Optional[List[TitleVariant]] = None,
    ) -> SEOPackage:
        """Construct full high-conversion SEO packaging and persist to SQLite."""
        project = self.repository.get_video_project(project_id)
        if not project:
            raise ValueError(f"VideoProject '{project_id}' not found.")

        channel = self.repository.get_channel(project.channel_id)
        handle = channel.handle if channel else "@YouTube"

        # 1. Title variants and selected title (favor tournament-grounded variants if provided)
        if not title_variants:
            title_variants = self._build_title_variants(primary_keyword, series_context)
        if not selected_title:
            selected_title = title_variants[1].title if len(title_variants) > 1 else title_variants[0].title

        # 2. Extract Chapters from Script Scenes
        chapters = self._extract_chapters(project)

        # 3. Cluster and Bound Tags (<= 500 chars)
        tags = self._cluster_tags(primary_keyword, channel.niche if channel else "Tech")

        # 4. Assemble High-Conversion Description
        description = self._assemble_description(
            project=project,
            primary_keyword=primary_keyword,
            chapters=chapters,
            channel_handle=handle,
            series_context=series_context,
            sources_summary=sources_summary,
        )

        # 5. Craft Engagement Pinned Comment
        pinned_comment = self._craft_pinned_comment(primary_keyword)

        package = SEOPackage(
            id=f"seo-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            primary_keyword=primary_keyword,
            title_variants=title_variants,
            selected_title=selected_title,
            description=description,
            chapters=chapters,
            tags=tags,
            pinned_comment=pinned_comment,
            created_at=datetime.now(timezone.utc),
        )

        self.repository.save_seo_package(package)
        return package

    def _build_title_variants(
        self, primary_keyword: str, series_context: Optional[Dict[str, Any]] = None
    ) -> List[TitleVariant]:
        """Emergency fallback title variants when reasoning model is bypassed or unavailable."""
        clean_kw = primary_keyword.strip()


        # Variant 1: Curiosity Gap
        v1_title = f"The Secret Truth About {clean_kw}"
        if len(v1_title) > 95:
            v1_title = f"The Truth About {clean_kw}"[:95]
        v1 = TitleVariant(
            angle=TitleVariantType.CURIOSITY_GAP,
            title=v1_title,
            predicted_ctr_rationale="Triggers the information gap theory by hinting at non-public or overlooked truth.",
        )

        # Variant 2: Direct Value / Benefit
        v2_title = f"How {clean_kw} Changes Everything (Fast Guide)"
        if len(v2_title) > 95:
            v2_title = f"{clean_kw}: Everything You Need to Know"[:95]
        v2 = TitleVariant(
            angle=TitleVariantType.DIRECT_VALUE,
            title=v2_title,
            predicted_ctr_rationale="Promises direct actionable understanding in minimal time.",
        )

        # Variant 3: Provocative / Counter-Intuitive Question
        v3_title = f"Is {clean_kw} Actually Overrated?"
        if len(v3_title) > 95:
            v3_title = f"Why {clean_kw} Might Fail?"[:95]
        v3 = TitleVariant(
            angle=TitleVariantType.PROVOCATIVE_QUESTION,
            title=v3_title,
            predicted_ctr_rationale="Challenges viewer confirmation bias, creating cognitive dissonance that drives clicks.",
        )

        return [v1, v2, v3]

    def _extract_chapters(self, project: VideoProject) -> List[Chapter]:
        """Derive formatted timestamp chapters from script scenes."""
        chapters: List[Chapter] = []
        if not project.script or not project.script.scenes:
            return [Chapter(timestamp_seconds=0.0, timestamp_formatted="00:00", title="Overview")]

        cumulative_seconds = 0.0
        for i, scene in enumerate(project.script.scenes):
            minutes = int(cumulative_seconds // 60)
            secs = int(cumulative_seconds % 60)
            formatted = f"{minutes:02d}:{secs:02d}"

            raw_title = scene.hook or f"Part {i + 1}"
            clean_title = raw_title.replace("\n", " ").strip()
            if len(clean_title) > 40:
                clean_title = clean_title[:37] + "..."

            chapters.append(
                Chapter(
                    timestamp_seconds=round(cumulative_seconds, 2),
                    timestamp_formatted=formatted,
                    title=clean_title,
                )
            )
            cumulative_seconds += scene.target_duration_seconds

        return chapters

    def _cluster_tags(self, primary_keyword: str, niche: str) -> List[str]:
        """Generate clustered tags while strictly obeying YouTube's 500-character limit."""
        words = primary_keyword.split()
        base_candidates = [
            primary_keyword,
            niche,
            f"{primary_keyword} tutorial",
            f"{primary_keyword} explained",
            "Tech Breakdown",
            "Software Architecture",
            "AI 2026",
            "Deep Dive",
            "Engineering",
        ]
        if len(words) > 1:
            base_candidates.extend(words)

        clustered: List[str] = []
        total_len = 0
        for tag in base_candidates:
            clean_t = tag.strip()
            if not clean_t or clean_t in clustered:
                continue
            # +1 for comma separator
            if total_len + len(clean_t) + 1 <= 480:
                clustered.append(clean_t)
                total_len += len(clean_t) + 1

        return clustered

    def _assemble_description(
        self,
        project: VideoProject,
        primary_keyword: str,
        chapters: List[Chapter],
        channel_handle: str,
        series_context: Optional[Dict[str, Any]] = None,
        sources_summary: Optional[str] = None,
    ) -> str:
        """Construct full YouTube description layout with above-the-fold hook, chapters, and citations."""
        lines = []

        # 1. Above-the-fold hook
        hook_text = project.script.hook if project.script and project.script.hook else f"Discover how {primary_keyword} works."
        lines.append(hook_text)
        lines.append("")
        lines.append(f"In this episode, we break down {primary_keyword} from the ground up.")
        lines.append("")

        # 2. Chapters
        if chapters:
            lines.append("⏱️ TIMESTAMPS:")
            for ch in chapters:
                lines.append(f"{ch.timestamp_formatted} - {ch.title}")
            lines.append("")

        # 3. Series context
        if series_context and "series_title" in series_context:
            stitle = series_context["series_title"]
            ep = series_context.get("current_episode") or series_context.get("episode_number")
            lines.append(f"📺 Series: {stitle} (Episode {ep})")
            if series_context.get("call_to_action"):
                lines.append(series_context["call_to_action"])
            lines.append("")

        # 4. Provenance & citations
        if sources_summary:
            lines.append("🔬 RESEARCH & CITATIONS:")
            lines.append(sources_summary)
            lines.append("")

        # 5. Channel signature
        lines.append(f"Subscribe to {channel_handle} for daily deep tech breakdowns!")
        lines.append("#Shorts #Tech #AI #Coding")

        return "\n".join(lines)

    def _craft_pinned_comment(self, primary_keyword: str) -> str:
        """Generate high-engagement algorithmic comment question."""
        return (
            f"What is your biggest question about {primary_keyword}? "
            f"Drop your thoughts below and let's discuss in the comments! 👇"
        )
