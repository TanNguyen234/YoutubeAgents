from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.db.repository import SQLiteRepository
from app.domain.enums import (
    ClaimVerificationVerdict,
    ContentFormat,
    PlatformFormat,
    PrimaryVideoGoal,
    QualityStatus,
    TonePreset,
    VideoLifecycleState,
)
from app.domain.models import (
    Channel,
    Claim,
    FactCheckReport,
    ResearchDossier,
    ResearchSource,
    Scene,
    ScriptSections,
    VideoCreativeBrief,
    VideoProject,
)
from app.services.pipeline_brain import BrainPipeline
from app.services.retention_planner import RetentionPlanner


@pytest.fixture
def mock_pipeline_env(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "test.db")
    channel = Channel(
        id="chan_brief_test",
        title="Engineering Deep Dives",
        handle="@DeepDives",
        niche="Systems Architecture",
        target_audience="Software Engineers",
    )
    repo.save_channel(channel)
    brain = BrainPipeline(repository=repo)
    brain.strategist.duplicate_detector.check_duplicate = MagicMock(return_value=(False, 0.0, None))
    brain.evaluator.evaluate_topic_with_reasoning = MagicMock(return_value=(
        {"demand": 8.0, "freshness": 7.0, "competition": 3.0, "channel_fit": 8.0, "originality": 7.5, "evidence_quality": 9.0, "production_feasibility": 8.0},
        "Strong opportunity",
        {"volume_source": "seed"},
    ))

    def make_sections(*args, **kwargs):
        return ScriptSections(
            hook="SQLite concurrency is fundamentally misunderstood.",
            intro="Let's unpack how WAL mode changes locking.",
            segments=[
                Scene(index=0, hook="Hook", narration="Think of a WAL like an append-only journal in production.", target_duration_seconds=15.0),
                Scene(index=1, hook="Payoff", narration="WAL readers never block writers in production.", target_duration_seconds=15.0),
            ],
            cta="WAL eliminates reader-writer locks, but checkpointing is the next bottleneck.",
            estimated_duration=30.0,
        )

    brain.generator.generate_script_sections = MagicMock(side_effect=make_sections)
    brain.extractor.extract_from_script = MagicMock(return_value=[
        Claim(id="c1", statement="WAL readers never block writers in production.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True, source_id="s1")
    ])

    def mock_verify_all(claims, dossier, project_id, **kwargs):
        return FactCheckReport(
            id=f"fcr_{project_id}",
            project_id=project_id,
            audit_summary="Pass.",
            claims=[
                Claim(id="c1", statement="WAL readers never block writers in production.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True)
            ],
            verified_count=1,
            failed_count=0,
            overall_verdict=QualityStatus.PASSED,
        )

    brain.checker.verify_all_claims = MagicMock(side_effect=mock_verify_all)
    return brain, repo, channel


def test_pipeline_resolves_creative_brief_with_real_dossier(mock_pipeline_env):
    """Pipeline resolves default brief extracting misconception and failure from dossier evidence."""
    brain, repo, channel = mock_pipeline_env
    dossier = ResearchDossier(
        id="dos_test_01",
        topic_id="top_test_01",
        topic="SQLite Concurrency",
        summary="Dossier on SQLite WAL.",
        sources=[
            ResearchSource(
                id="s1",
                title="SQLite WAL Guide",
                url="https://sqlite.org/wal.html",
                content_snapshot="A widespread misconception is that WAL mode eliminates all locking overhead. In reality, checkpointing causes a major bottleneck under high write load.",
                content_sha256="abc123456789",
            )
        ],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=dossier)

    with patch.object(RetentionPlanner, "build_blueprint", side_effect=RetentionPlanner().build_blueprint) as mock_build_bp:
        project, report = brain.run_stage_1_to_5(
            project_id="proj_brief_01",
            channel=channel,
            keyword="SQLite Concurrency",
            seed_urls=["https://sqlite.org/wal.html"],
            content_format=ContentFormat.EXPLAINER,
        )

        assert mock_build_bp.called
        passed_brief = mock_build_bp.call_args.kwargs["brief"]
        assert passed_brief is not None
        assert passed_brief.common_misconception is not None
        assert "misconception is that wal mode eliminates all locking overhead" in passed_brief.common_misconception.lower()
        assert passed_brief.common_failure is not None
        assert "bottleneck" in passed_brief.common_failure.lower()


def test_pipeline_uses_project_platform_format_for_default_duration(mock_pipeline_env):
    """Pipeline uses project.format to select correct duration defaults (e.g. 240s for LONG_FORM_16_9)."""
    brain, repo, channel = mock_pipeline_env
    project = VideoProject(
        id="proj_brief_long",
        channel_id=channel.id,
        title="Database Internals",
        state=VideoLifecycleState.CREATED,
        format=PlatformFormat.LONG_FORM_16_9,
        content_format=ContentFormat.EXPLAINER,
    )
    repo.save_video_project(project)

    dossier = ResearchDossier(
        id="dos_long",
        topic_id="top_long",
        topic="Database Internals",
        summary="Summary",
        sources=[],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=dossier)

    with patch.object(RetentionPlanner, "build_blueprint", side_effect=RetentionPlanner().build_blueprint) as mock_build_bp:
        brain.run_stage_1_to_5(
            project_id="proj_brief_long",
            channel=channel,
            keyword="Database Internals",
            seed_urls=["https://example.com"],
        )

        passed_brief = mock_build_bp.call_args.kwargs["brief"]
        assert passed_brief.target_duration_seconds == 240.0


def test_explicit_creative_brief_values_are_preserved(mock_pipeline_env):
    """User-supplied explicit brief fields must never be overwritten by defaults or dossier."""
    brain, repo, channel = mock_pipeline_env
    dossier = ResearchDossier(
        id="dos_explicit",
        topic_id="top_explicit",
        topic="SQLite Concurrency",
        summary="Summary",
        sources=[
            ResearchSource(
                id="s1",
                title="Doc",
                url="https://example.com",
                content_snapshot="A misconception is that SQLite is slow. A failure is corruption.",
                content_sha256="abc123456789",
            )
        ],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=dossier)

    user_brief = VideoCreativeBrief(
        target_duration_seconds=120.0,
        primary_goal=PrimaryVideoGoal.LEAD_GENERATION,
        tone=TonePreset.INVESTIGATIVE,
        desired_viewer_emotion="urgency and caution",
        common_misconception="Developers assume SQLite cannot handle concurrent reads",
        common_failure="Thread starvation when checkpointing is delayed",
    )

    with patch.object(RetentionPlanner, "build_blueprint", side_effect=RetentionPlanner().build_blueprint) as mock_build_bp:
        brain.run_stage_1_to_5(
            project_id="proj_explicit",
            channel=channel,
            keyword="SQLite Concurrency",
            seed_urls=["https://example.com"],
            creative_brief=user_brief,
        )

        passed_brief = mock_build_bp.call_args.kwargs["brief"]
        assert passed_brief.target_duration_seconds == 120.0
        assert passed_brief.primary_goal == PrimaryVideoGoal.LEAD_GENERATION
        assert passed_brief.tone == TonePreset.INVESTIGATIVE
        assert passed_brief.desired_viewer_emotion == "urgency and caution"
        assert passed_brief.common_misconception == "Developers assume SQLite cannot handle concurrent reads"
        assert passed_brief.common_failure == "Thread starvation when checkpointing is delayed"


def test_missing_brief_misconception_is_enriched_from_dossier(mock_pipeline_env):
    """If user brief provides common_failure but common_misconception is None, enrich only misconception."""
    brain, repo, channel = mock_pipeline_env
    dossier = ResearchDossier(
        id="dos_enrich_m",
        topic_id="top_enrich_m",
        topic="SQLite Concurrency",
        summary="Summary",
        sources=[
            ResearchSource(
                id="s1",
                title="Doc",
                url="https://example.com",
                content_snapshot="A common myth is that WAL mode is dangerous for web applications. The bottleneck is disk I/O.",
                content_sha256="abc123456789",
            )
        ],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=dossier)

    user_brief = VideoCreativeBrief(
        target_duration_seconds=90.0,
        primary_goal=PrimaryVideoGoal.EDUCATE,
        common_misconception=None,
        common_failure="Explicit user failure: lock escalation timeout",
    )

    with patch.object(RetentionPlanner, "build_blueprint", side_effect=RetentionPlanner().build_blueprint) as mock_build_bp:
        brain.run_stage_1_to_5(
            project_id="proj_enrich_m",
            channel=channel,
            keyword="SQLite Concurrency",
            seed_urls=["https://example.com"],
            creative_brief=user_brief,
        )

        passed_brief = mock_build_bp.call_args.kwargs["brief"]
        assert passed_brief.common_failure == "Explicit user failure: lock escalation timeout"
        assert passed_brief.common_misconception is not None
        assert "myth is that wal mode is dangerous" in passed_brief.common_misconception.lower()


def test_missing_brief_failure_is_enriched_from_dossier(mock_pipeline_env):
    """If user brief provides misconception but common_failure is None, enrich only failure."""
    brain, repo, channel = mock_pipeline_env
    dossier = ResearchDossier(
        id="dos_enrich_f",
        topic_id="top_enrich_f",
        topic="SQLite Concurrency",
        summary="Summary",
        sources=[
            ResearchSource(
                id="s1",
                title="Doc",
                url="https://example.com",
                content_snapshot="Dossier evidence shows that excessive WAL file growth causes a major latency spike and crash during busy transactions.",
                content_sha256="abc123456789",
            )
        ],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=dossier)

    user_brief = VideoCreativeBrief(
        target_duration_seconds=90.0,
        primary_goal=PrimaryVideoGoal.EDUCATE,
        common_misconception="Explicit misconception: users think SQLite has no WAL",
        common_failure=None,
    )

    with patch.object(RetentionPlanner, "build_blueprint", side_effect=RetentionPlanner().build_blueprint) as mock_build_bp:
        brain.run_stage_1_to_5(
            project_id="proj_enrich_f",
            channel=channel,
            keyword="SQLite Concurrency",
            seed_urls=["https://example.com"],
            creative_brief=user_brief,
        )

        passed_brief = mock_build_bp.call_args.kwargs["brief"]
        assert passed_brief.common_misconception == "Explicit misconception: users think SQLite has no WAL"
        assert passed_brief.common_failure is not None
        assert any(k in passed_brief.common_failure.lower() for k in ["latency spike", "crash"])


def test_no_evidence_leaves_misconception_and_failure_none(mock_pipeline_env):
    """If dossier has no evidence of misconception or failure, both remain None without fabrication."""
    brain, repo, channel = mock_pipeline_env
    dossier = ResearchDossier(
        id="dos_neutral",
        topic_id="top_neutral",
        topic="General Computing",
        summary="General summary of standard features.",
        sources=[
            ResearchSource(
                id="s1",
                title="Doc",
                url="https://example.com",
                content_snapshot="The system initializes standard memory buffers and opens the connection pool normally.",
                content_sha256="abc123456789",
            )
        ],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=dossier)

    with patch.object(RetentionPlanner, "build_blueprint", side_effect=RetentionPlanner().build_blueprint) as mock_build_bp:
        brain.run_stage_1_to_5(
            project_id="proj_neutral",
            channel=channel,
            keyword="General Computing",
            seed_urls=["https://example.com"],
        )

        passed_brief = mock_build_bp.call_args.kwargs["brief"]
        assert passed_brief.common_misconception is None
        assert passed_brief.common_failure is None


def test_revenue_goal_reaches_script_generator(mock_pipeline_env):
    """PrimaryVideoGoal.REVENUE flows through pipeline into generator and hook tournament."""
    brain, repo, channel = mock_pipeline_env
    dossier = ResearchDossier(
        id="dos_rev",
        topic_id="top_rev",
        topic="Cloud Database Costs",
        summary="Cost breakdown.",
        sources=[],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=dossier)

    revenue_brief = VideoCreativeBrief(
        primary_goal=PrimaryVideoGoal.REVENUE,
    )

    with patch.object(brain.generator, "generate_script_sections") as mock_gen_sec:
        mock_gen_sec.side_effect = lambda **kwargs: ScriptSections(
            hook="Hook",
            intro="Intro",
            segments=[Scene(index=0, narration="Commercial ROI explanation.", target_duration_seconds=15.0)],
            cta="Explore our database optimization toolkit for enterprise ROI.",
            estimated_duration=15.0,
        )
        brain.run_stage_1_to_5(
            project_id="proj_rev",
            channel=channel,
            keyword="Cloud Database Costs",
            seed_urls=["https://example.com"],
            creative_brief=revenue_brief,
        )

        assert mock_gen_sec.called
        passed_brief = mock_gen_sec.call_args.kwargs["brief"]
        assert passed_brief.primary_goal == PrimaryVideoGoal.REVENUE
        assert "commercial" in passed_brief.desired_viewer_emotion.lower() or "roi" in passed_brief.desired_viewer_emotion.lower()


def test_full_lifecycle_accepts_creative_brief(mock_pipeline_env):
    """run_full_autonomous_lifecycle accepts creative_brief and propagates it."""
    brain, repo, channel = mock_pipeline_env
    dossier = ResearchDossier(
        id="dos_full",
        topic_id="top_full",
        topic="SQLite Concurrency",
        summary="Summary",
        sources=[],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=dossier)

    custom_brief = VideoCreativeBrief(
        target_duration_seconds=75.0,
        primary_goal=PrimaryVideoGoal.AUTHORITY,
        tone=TonePreset.TECHNICAL,
    )

    with patch.object(brain, "run_stage_1_to_5") as mock_stage_1_5:
        dummy_project = VideoProject(
            id="proj_full",
            channel_id=channel.id,
            title="SQLite Concurrency",
            state=VideoLifecycleState.BLOCKED,  # Block so it halts before media production
        )
        mock_stage_1_5.return_value = (dummy_project, MagicMock())

        res = brain.run_full_autonomous_lifecycle(
            project_id="proj_full",
            channel=channel,
            keyword="SQLite Concurrency",
            seed_urls=["https://example.com"],
            creative_brief=custom_brief,
        )

        assert mock_stage_1_5.called
        assert mock_stage_1_5.call_args.kwargs["creative_brief"] == custom_brief
        assert res["lifecycle_state"] == VideoLifecycleState.BLOCKED.value
