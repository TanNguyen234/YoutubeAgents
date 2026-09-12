"""Unit tests for ScriptRetentionEvaluator and retention-aware script generation."""

import pytest

from app.domain.enums import ContentFormat, HookAngle, RetentionCueType
from app.domain.models import (
    Channel,
    Claim,
    HookCandidate,
    ResearchDossier,
    ResearchSource,
    Scene,
    Script,
    ScriptSections,
    VideoCreativeBrief,
)
from app.services.retention_planner import RetentionPlanner
from app.services.script_generator import ScriptGenerator
from app.services.script_retention import ScriptRetentionEvaluator


@pytest.fixture
def sample_hook():
    return HookCandidate(
        text="There's a subtle lock in SQLite that silently freezes concurrent readers.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Expose the hidden locking mechanism and demonstrate how WAL eliminates reader blocking.",
    )


@pytest.fixture
def sample_blueprint(sample_hook):
    planner = RetentionPlanner()
    brief = VideoCreativeBrief(target_duration_seconds=40.0)
    return planner.build_blueprint(
        hook=sample_hook,
        brief=brief,
        content_format=ContentFormat.EXPLAINER,
        topic="SQLite Concurrency",
    )


def test_open_loop_without_close_fails_retention_qa(sample_hook, sample_blueprint):
    """If script never resolves the opening hook promise, retention QA flags unclosed loop and fails."""
    evaluator = ScriptRetentionEvaluator()

    # Script talks about unrelated things at the end (e.g., frontend CSS)
    unresolved_script = Script(
        id="scr_unresolved",
        title="SQLite Locking",
        hook=sample_hook.text,
        scenes=[
            Scene(index=0, narration="Databases often suffer from contention when reads and writes collide.", target_duration_seconds=8.0),
            Scene(index=1, narration="In traditional mode, a single write lock blocks every query.", target_duration_seconds=10.0),
            Scene(index=2, narration="Anyway, web development also requires good CSS styling and responsive layouts.", target_duration_seconds=12.0),
            Scene(index=3, narration="Make sure your buttons have rounded corners and nice drop shadows.", target_duration_seconds=10.0),
        ],
        total_word_count=55,
        estimated_duration_seconds=40.0,
        content_format=ContentFormat.EXPLAINER,
        sections=ScriptSections(
            hook=sample_hook.text,
            intro="Databases often suffer from contention.",
            segments=[
                Scene(index=0, narration="Databases often suffer from contention when reads and writes collide.", target_duration_seconds=8.0),
                Scene(index=1, narration="In traditional mode, a single write lock blocks every query.", target_duration_seconds=10.0),
                Scene(index=2, narration="Anyway, web development also requires good CSS styling and responsive layouts.", target_duration_seconds=12.0),
                Scene(index=3, narration="Make sure your buttons have rounded corners and nice drop shadows.", target_duration_seconds=10.0),
            ],
            cta="Follow for more web design tips.",
            estimated_duration=40.0,
        ),
    )

    report = evaluator.evaluate(unresolved_script, blueprint=sample_blueprint)

    assert report.passed is False
    assert any("HOOK_PROMISE_NOT_RESOLVED" in iss for iss in report.issues)
    assert any(r.severity == "HIGH" for r in report.drop_risks)
    assert report.payoff_alignment_score < 0.65


def test_resolved_promise_passes_retention_qa(sample_hook, sample_blueprint):
    """Script cleanly resolving opening hook promise in final scenes passes retention QA."""
    evaluator = ScriptRetentionEvaluator()

    resolved_script = Script(
        id="scr_resolved",
        title="SQLite Locking",
        hook=sample_hook.text,
        scenes=[
            Scene(index=0, narration="In default rollback journal mode, writing acquires an exclusive table lock.", target_duration_seconds=8.0),
            Scene(index=1, narration="When a transaction writes, all concurrent readers are forced to wait.", target_duration_seconds=10.0),
            Scene(index=2, narration="Switching to WAL mode writes new pages to a separate log file instead.", target_duration_seconds=10.0),
            Scene(index=3, narration="This eliminates reader blocking entirely, unlocking massive concurrent read throughput.", target_duration_seconds=10.0),
        ],
        total_word_count=60,
        estimated_duration_seconds=38.0,
        content_format=ContentFormat.EXPLAINER,
        sections=ScriptSections(
            hook=sample_hook.text,
            intro="In default mode writing acquires an exclusive table lock.",
            segments=[
                Scene(index=0, narration="In default rollback journal mode, writing acquires an exclusive table lock.", target_duration_seconds=8.0),
                Scene(index=1, narration="When a transaction writes, all concurrent readers are forced to wait.", target_duration_seconds=10.0),
                Scene(index=2, narration="Switching to WAL mode writes new pages to a separate log file instead.", target_duration_seconds=10.0),
                Scene(index=3, narration="This eliminates reader blocking entirely, unlocking massive concurrent read throughput.", target_duration_seconds=10.0),
            ],
            cta="Subscribe for more deep systems engineering breakdowns.",
            estimated_duration=38.0,
        ),
    )

    report = evaluator.evaluate(resolved_script, blueprint=sample_blueprint)

    assert report.passed is True
    assert report.payoff_alignment_score >= 0.85
    assert not any(r.severity == "HIGH" for r in report.drop_risks)


def test_abrupt_cta_before_payoff_is_flagged(sample_hook, sample_blueprint):
    """Premature call-to-action in the middle of the narrative triggers HIGH drop risk."""
    evaluator = ScriptRetentionEvaluator()

    premature_cta_script = Script(
        id="scr_premature_cta",
        title="SQLite Locking",
        hook=sample_hook.text,
        scenes=[
            Scene(index=0, narration="Before we explain WAL, hit subscribe and like this video right now!", target_duration_seconds=8.0),
            Scene(index=1, narration="In default mode, write locks freeze all queries.", target_duration_seconds=10.0),
            Scene(index=2, narration="WAL mode writes pages to a separate log, eliminating reader blocking.", target_duration_seconds=12.0),
        ],
        total_word_count=45,
        estimated_duration_seconds=30.0,
        content_format=ContentFormat.EXPLAINER,
        sections=ScriptSections(
            hook=sample_hook.text,
            intro="Before we explain WAL, hit subscribe and like this video right now!",
            segments=[
                Scene(index=0, narration="Before we explain WAL, hit subscribe and like this video right now!", target_duration_seconds=8.0),
                Scene(index=1, narration="In default mode, write locks freeze all queries.", target_duration_seconds=10.0),
                Scene(index=2, narration="WAL mode writes pages to a separate log, eliminating reader blocking.", target_duration_seconds=12.0),
            ],
            cta="Thanks for watching.",
            estimated_duration=30.0,
        ),
    )

    report = evaluator.evaluate(premature_cta_script, blueprint=sample_blueprint)

    assert report.passed is False
    assert any("CTA_BEFORE_PAYOFF" in iss for iss in report.issues)
    assert any(r.severity == "HIGH" and "premature" in r.reason.lower() for r in report.drop_risks)


def test_selected_hook_is_used_by_script_generation(sample_hook, sample_blueprint):
    """Selected hook from tournament is strictly placed into ScriptSections.hook."""
    class MockGenBackend:
        def generate_structured(self, prompt, schema):
            return ScriptSections(
                hook=sample_hook.text,
                intro="intro",
                segments=[
                    Scene(index=0, narration="narr", target_duration_seconds=10.0, visual_prompt="vis")
                ],
                cta="cta",
                estimated_duration=30.0,
            )
    generator = ScriptGenerator(backend=MockGenBackend())
    channel = Channel(
        id="chan_1",
        title="Systems Code",
        handle="@SystemsCode",
        niche="Software Architecture",
        target_audience="Software Engineers",
    )
    dossier = ResearchDossier(
        id="dos_1",
        topic_id="top_1",
        topic="SQLite Concurrency",
        summary="SQLite WAL documentation and benchmarks.",
        sources=[
            ResearchSource(
                id="s1",
                title="SQLite WAL",
                url="https://sqlite.org/wal.html",
                content_snapshot="In WAL mode, readers do not block writers and writers do not block readers.",
                content_sha256="dummy_sha",
            )
        ],
    )

    sections = generator.generate_script_sections(
        channel=channel,
        keyword="SQLite Concurrency",
        dossier=dossier,
        content_format=ContentFormat.EXPLAINER,
        hook=sample_hook,
        blueprint=sample_blueprint,
    )

    assert sections.hook == sample_hook.text


def test_script_visual_prompt_describes_observable_content_not_aesthetic_style():
    """ScriptGenerator prompt rules enforce observable visual actions rather than neon/cyberpunk slop."""
    generator = ScriptGenerator()
    channel = Channel(
        id="chan_1",
        title="Systems Code",
        handle="@SystemsCode",
        niche="Software Architecture",
        target_audience="Software Engineers",
    )
    dossier = ResearchDossier(
        id="dos_1",
        topic_id="top_1",
        topic="SQLite Concurrency",
        summary="WAL summary",
        sources=[],
    )

    # Inspect the prompt constructed by checking prompt content or using a dummy backend
    class SpyBackend:
        def __init__(self):
            self.last_prompt = ""

        def generate_structured(self, prompt, schema):
            self.last_prompt = prompt
            return ScriptSections(
                hook="Test hook line.",
                intro="Test intro.",
                segments=[
                    Scene(
                        index=0,
                        narration="Test narration segment.",
                        visual_prompt="Show reader thread continuing query while writer appends to WAL file.",
                        target_duration_seconds=10.0,
                    )
                ],
                cta="Subscribe.",
                estimated_duration=30.0,
            )

    spy = SpyBackend()
    generator.backend = spy

    generator.generate_script_sections(
        channel=channel,
        keyword="SQLite Concurrency",
        dossier=dossier,
    )

    prompt = spy.last_prompt
    assert "DO NOT prescribe glowing UI, neon code, cyberpunk aesthetics" in prompt
    assert "OBSERVABLE VISUAL ACTION" in prompt


def test_fact_rewrite_runs_final_retention_qa(monkeypatch, tmp_path):
    """Fact check and rewrite loop must execute final retention QA on the resulting script."""
    from unittest.mock import MagicMock, patch
    from app.db.repository import SQLiteRepository
    from app.domain.enums import ClaimVerificationVerdict, QualityStatus
    from app.domain.models import FactCheckReport, QualityResult
    from app.services.pipeline_brain import BrainPipeline

    db_path = tmp_path / "test_brain.db"
    repo = SQLiteRepository(str(db_path))
    brain = BrainPipeline(repository=repo)

    channel = Channel(
        id="chan_fact_test",
        title="Database Systems",
        handle="@DBSystems",
        niche="Database Architecture",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    eval_calls = []
    original_eval = ScriptRetentionEvaluator.evaluate

    def mock_eval(self, script, blueprint=None):
        eval_calls.append(script.id)
        return original_eval(self, script, blueprint=blueprint)

    monkeypatch.setattr(ScriptRetentionEvaluator, "evaluate", mock_eval)

    # Mock stage 1 research
    mock_dossier = ResearchDossier(
        id="dos_test",
        topic_id="top_test",
        topic="SQLite Concurrency",
        summary="WAL allows concurrent readers.",
        sources=[
            ResearchSource(
                id="s1",
                title="SQLite WAL",
                url="https://sqlite.org/wal.html",
                content_snapshot="In WAL mode, readers do not block writers.",
                content_sha256="dummy_sha",
            )
        ],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=mock_dossier)
    brain.evaluator.evaluate_topic_with_reasoning = MagicMock(return_value=(
        {
            "demand": 8.0,
            "freshness": 7.0,
            "competition": 3.0,
            "channel_fit": 8.0,
            "originality": 7.5,
            "evidence_quality": 9.0,
            "production_feasibility": 8.0,
        },
        "Strong niche opportunity",
        {"volume_source": "seed"},
    ))

    mock_scenes = [
        Scene(index=0, hook="SQLite concurrency is misunderstood.", narration="WAL mode changes locking.", target_duration_seconds=15.0, visual_prompt="Show WAL log write"),
        Scene(index=1, hook="The payoff.", narration="WAL readers never block writers in production.", target_duration_seconds=15.0, visual_prompt="Show concurrent read/write"),
    ]
    mock_sections = ScriptSections(
        hook="SQLite concurrency is misunderstood.",
        intro="Let's unpack how WAL mode changes locking.",
        segments=mock_scenes,
        cta="Subscribe for more database engineering.",
        estimated_duration=30.0,
    )
    brain.generator.generate_script_sections = MagicMock(return_value=mock_sections)
    brain.generator.rewrite_script_sections = MagicMock(return_value=mock_sections)
    brain.extractor.extract_from_script = MagicMock(return_value=[
        Claim(id="c1", statement="SQLite speeds up 100x.", verified=False, source_id="s1")
    ])

    # Mock HookTournamentService to prevent CLI subprocess execution in unit test
    sample_hook = HookCandidate(
        text="SQLite concurrency is misunderstood.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Unpack WAL concurrency.",
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.generate_hook_candidates",
        lambda *args, **kwargs: [sample_hook],
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.run_tournament",
        lambda *args, **kwargs: (sample_hook, MagicMock(), [MagicMock()]),
    )

    # Mock fact checker to trigger one rewrite attempt
    call_count = {"count": 0}
    def mock_verify(*args, **kwargs):
        call_count["count"] += 1
        if call_count["count"] == 1:
            # First pass: requires rewrite
            return FactCheckReport(
                id="fcr_test_1",
                project_id="proj_fact_ret",
                audit_summary="Initial verification found ungrounded claim.",
                claims=[
                    Claim(id="c1", statement="SQLite speeds up 100x.", verdict=ClaimVerificationVerdict.REWRITE_REQUIRED, verified=False)
                ],
                verified_count=0,
                failed_count=1,
                overall_verdict=QualityStatus.FAILED,
            )
        else:
            # Second pass after rewrite: passed
            return FactCheckReport(
                id="fcr_test_2",
                project_id="proj_fact_ret",
                audit_summary="Rewrite verified against SQLite documentation.",
                claims=[
                    Claim(id="c1", statement="WAL allows concurrent readers.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True)
                ],
                verified_count=1,
                failed_count=0,
                overall_verdict=QualityStatus.PASSED,
            )

    brain.checker.verify_all_claims = MagicMock(side_effect=mock_verify)

    project, report = brain.run_stage_1_to_5(
        project_id="proj_fact_ret",
        channel=channel,
        keyword="SQLite Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
        max_rewrite_attempts=1,
    )

    # Initial retention QA was run, and final retention QA was run after factual rewrite
    assert len(eval_calls) >= 2

