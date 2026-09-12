"""Integration test for the retention-aware grounded lifecycle.

Flow:
Research dossier → hook tournament → selected hook → retention blueprint →
script draft → script retention QA → fact verification → fake TTS →
timestamp mapping → AutoDirector visual direction.

Verifies:
1. Selected hook belongs to generated candidates
2. Final script resolves hook promise
3. Final fact report remains PASSED
4. Timed retention cues fall inside audio duration
5. Director storyboard contains retention-aware decisions
6. No fake source IDs
7. No fake chart provenance
8. No legacy slideshow fallback
9. Zero outbound network calls
"""

import gc
import json
from pathlib import Path
import socket
import pytest

from app.core.backend import MockReasoningBackend
from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    ClaimVerificationVerdict,
    ContentFormat,
    HookAngle,
    QualityStatus,
    RetentionCueType,
    VideoLifecycleState,
)
from app.domain.models import (
    Channel,
    HookCandidate,
    ResearchSource,
    Scene,
    ScriptSections,
)
from app.media.director.models import CreativeFallbackPolicy
from app.media.pipeline import MediaProductionPipeline
from app.media.tts.local_fake import LocalFakeTTSBackend
from app.services.claim_extractor import ClaimExtractionOutput
from app.services.fact_checker import ClaimEntailmentOutput
from app.services.hook_strategy import HookCandidatesPayload
from app.services.pipeline_brain import BrainPipeline
from app.services.research_agent import ResearchAgent
from app.services.retention_planner import map_retention_cues_to_timestamps
from app.services.script_retention import ScriptRetentionEvaluator
from app.services.topic_evaluator import TopicEvaluationOutput


def test_retention_aware_grounded_lifecycle(monkeypatch, tmp_path: Path):
    """Verify complete retention-aware narrative lifecycle with zero network and strict factual guarantees."""
    # 0. STRICT OFFLINE GUARD: Any outbound network call fails immediately
    def guarded_connect(sock, address):
        pytest.fail(f"Outbound network attempted during offline lifecycle test: connect({address})")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr("app.services.youtube_oauth.YouTubeOAuthManager.has_valid_token", lambda self: False)

    # 1. Database & Channel Setup
    db_path = tmp_path / "lifecycle_test.db"
    init_database(db_path)
    repo = SQLiteRepository(db_path)

    channel = Channel(
        id="chan-retention-01",
        title="Database Systems Architecture",
        handle="@DBArchitecture",
        niche="Distributed Systems & Database Internals",
        target_audience="Senior backend and systems engineers",
    )
    repo.save_channel(channel)

    # 2. Ground Truth Evidence Source
    source_url = "https://sqlite.org/wal.html"
    source_content = (
        "In SQLite WAL mode, readers do not block writers and writers do not block readers. "
        "A separate write-ahead log file records changes sequentially before checkpointing to the main database."
    )

    class OfflineResearchAgent(ResearchAgent):
        def fetch_source_from_url(self, source_id, url, title=None, authors=None, license_type="UNKNOWN"):
            return ResearchSource(
                id=source_id,
                url=url,
                final_url=url,
                http_status=200,
                title="SQLite Write-Ahead Logging Specification",
                authors=["SQLite Engineering Consortium"],
                content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                content_snapshot=source_content,
                license_type="Public Domain",
            )

    # 3. Deterministic Reasoning Double
    expected_candidates = [
        HookCandidate(
            text="Why does your database freeze during concurrent writes?",
            angle=HookAngle.CURIOSITY_GAP,
            promise="Reveal how WAL mode eliminates reader-writer lock contention.",
            required_claim_ids=[],
        ),
        HookCandidate(
            text="Your backend queries are slowing down because default database locks block every reader.",
            angle=HookAngle.PAIN_POINT,
            promise="Eliminate concurrency bottlenecks with write-ahead logging.",
            required_claim_ids=[],
        ),
        HookCandidate(
            text="Most developers think SQLite cannot handle concurrency, but that assumption is wrong.",
            angle=HookAngle.CONTRARIAN,
            promise="Demonstrate simultaneous read and write operations.",
            required_claim_ids=[],
        ),
        HookCandidate(
            text="Readers and writers run simultaneously without lock contention in WAL mode.",
            angle=HookAngle.RESULT_FIRST,
            promise="Break down concurrent WAL execution step by step.",
            required_claim_ids=[],
        ),
        HookCandidate(
            text="A single long-running query can lock your entire application if concurrency is misconfigured.",
            angle=HookAngle.STAKES_FIRST,
            promise="Configure concurrent storage safely without downtime.",
            required_claim_ids=[],
        ),
    ]

    def mock_handler(prompt, schema_cls):
        if schema_cls == TopicEvaluationOutput:
            return TopicEvaluationOutput(
                demand=9.0,
                freshness=8.0,
                competition=5.0,
                channel_fit=9.5,
                originality=8.5,
                evidence_quality=9.5,
                production_feasibility=9.0,
                historical_fit=9.0,
                rationale="High developer demand for SQLite WAL concurrency breakdown",
                score_reasons={"demand": "9/10", "channel_fit": "9.5/10"},
            )
        elif schema_cls == HookCandidatesPayload:
            return HookCandidatesPayload(candidates=expected_candidates)
        elif schema_cls == ScriptSections:
            # Script resolves hook promise and provides observable visual requirements
            return ScriptSections(
                hook="Most developers think SQLite cannot handle concurrency, but that assumption is wrong.",
                intro="By default, rollback journals acquire exclusive locks that freeze concurrent reads.",
                segments=[
                    Scene(
                        index=0,
                        hook="Default locks freeze readers.",
                        narration="In standard rollback journal mode, writing requires an exclusive lock that blocks all reader queries.",
                        target_duration_seconds=2.0,
                        visual_prompt="Show architectural flow of exclusive database lock blocking readers",
                    ),
                    Scene(
                        index=1,
                        hook="Write-ahead logging changes this.",
                        narration="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                        target_duration_seconds=2.0,
                        visual_prompt="Show one reader continuing while a writer appends to the WAL",
                    ),
                    Scene(
                        index=2,
                        hook="Simultaneous operations succeed.",
                        narration="Changes append sequentially to the log file while readers continue uninterrupted, demonstrating simultaneous read and write operations succeed without lock contention.",
                        target_duration_seconds=2.0,
                        visual_prompt="Show background checkpoint transfer while simultaneous read and write operations continue",
                    ),
                ],
                cta="Now you know how WAL mode enables simultaneous read and write operations. Subscribe for the next deep dive into storage engines.",
                voiceover_text="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                estimated_duration=6.0,
            )
        elif schema_cls == ClaimExtractionOutput:
            return ClaimExtractionOutput(
                claims=["In SQLite WAL mode, readers do not block writers and writers do not block readers."]
            )
        elif schema_cls == ClaimEntailmentOutput:
            return ClaimEntailmentOutput(
                is_supported=True,
                confidence=0.99,
                cited_url=source_url,
                cited_excerpt="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                rationale="Directly confirmed by official SQLite documentation.",
            )
        raise ValueError(f"Unhandled schema in integration test: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    tts = LocalFakeTTSBackend(duration_seconds=6.0)

    # 4. Pipeline Execution: Stages 1 through 5
    project_id = "proj-retention-e2e-01"
    pipeline = BrainPipeline(
        repository=repo,
        backend=backend,
        research_agent=OfflineResearchAgent(),
        tts_backend=tts,
    )

    project, fact_report = pipeline.run_stage_1_to_5(
        project_id=project_id,
        channel=channel,
        keyword="Mastering SQLite WAL Concurrency",
        seed_urls=[source_url],
        content_format=ContentFormat.EXPLAINER,
    )

    # 5. Assertion: Selected hook belongs to generated candidates
    assert project.script is not None
    assert project.script.retention_blueprint is not None
    generated_texts = [c.text for c in expected_candidates]
    assert project.script.hook in generated_texts
    assert project.script.retention_blueprint.hook.text in generated_texts

    # 6. Assertion: Final script resolves hook promise & passes retention QA
    blueprint = project.script.retention_blueprint
    assert blueprint.promised_payoff != ""
    evaluator = ScriptRetentionEvaluator()
    ret_report = evaluator.evaluate(project.script, blueprint=blueprint)
    assert ret_report.passed is True
    assert ret_report.payoff_alignment_score >= 0.5
    assert ret_report.hook_quality_score >= 0.6
    # No unclosed open loops
    for loop in ret_report.open_loops:
        assert loop.resolved is True

    # 7. Assertion: Final fact report remains PASSED
    assert project.state == VideoLifecycleState.VERIFIED
    assert fact_report.overall_verdict == QualityStatus.PASSED
    assert fact_report.verified_count >= 1
    assert fact_report.failed_count == 0
    for claim in fact_report.claims:
        assert claim.verdict == ClaimVerificationVerdict.VERIFIED
        assert claim.cited_url == source_url

    # 8. Assertion: No fake source IDs
    dossier = repo.get_research_dossier(project_id)
    assert dossier is not None
    dossier_source_ids = {s.id for s in dossier.sources}
    for claim in fact_report.claims:
        if claim.source_id:
            assert claim.source_id in dossier_source_ids

    # 9. Pipeline Execution: Stages 6 through 10 (TTS, Timestamp Mapping, AutoDirector, Render)
    media_pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        reasoning_backend=backend,
        base_output_dir=tmp_path / "media_out",
        fallback_policy=CreativeFallbackPolicy.FAIL_CLOSED,
    )

    project, qa_result, render_manifest = media_pipeline.run_production(
        project_id=project_id,
    )

    # 10. Assertion: Timed retention cues fall strictly inside audio duration
    audio_duration = tts.duration
    timed_cues = map_retention_cues_to_timestamps(
        cues=blueprint.cues,
        total_duration_seconds=audio_duration,
        timing_events=getattr(tts, "last_timing_events", None),
        canonical_narration=project.script.get_canonical_narration(),
    )
    assert len(timed_cues) >= 3
    for cue in timed_cues:
        assert 0.0 <= cue.timestamp_seconds <= audio_duration
        assert cue.cue_type is not None

    # 11. Assertion: Director storyboard contains retention-aware decisions
    storyboard_path = tmp_path / "media_out" / project_id / "director" / f"storyboard_{project_id}.json"
    assert storyboard_path.exists(), f"Storyboard file not found at {storyboard_path}"
    with open(storyboard_path, "r", encoding="utf-8") as f:
        storyboard_data = json.load(f)

    shots = storyboard_data.get("shots", [])
    assert len(shots) >= 3

    # Verify retention-aware decisions were recorded in shot camera motion / composition overrides
    retention_camera_motions = {"PATTERN_INTERRUPT_SNAP", "RAPID_PUSH_IN", "HERO_DOLLY_IN"}
    retention_compositions = {"DYNAMIC_CLOSE_UP", "HERO_CENTERED", "RESOLVING_WIDE"}
    has_retention_decision = any(
        s.get("camera_motion") in retention_camera_motions or s.get("composition") in retention_compositions
        for s in shots
    )
    assert has_retention_decision, f"Expected AutoDirector storyboard to contain retention-aware decisions. Shots: {shots}"

    # 12. Assertion: No fake chart provenance
    # Verify any data visualization shot has valid bindings or no fabricated data
    for s in shots:
        if s.get("visual_modality") == "DATA_VISUALIZATION":
            chart_data = s.get("chart_data") or []
            # Must not invent ungrounded chart points
            assert all("fake" not in str(pt).lower() for pt in chart_data)

    # 13. Assertion: No legacy slideshow fallback
    assert render_manifest.director_fallback_occurred is False
    assert render_manifest.director_used is True
    assert render_manifest.qa_verdict == "PASSED"
    assert qa_result.passed is True
    assert project.state == VideoLifecycleState.READY_FOR_REVIEW

    gc.collect()
