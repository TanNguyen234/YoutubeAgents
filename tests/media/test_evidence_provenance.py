"""Unit and integration tests for evidence provenance, citation binding, and quotation rules."""

from pathlib import Path
import pytest
from unittest.mock import MagicMock

from app.domain.enums import ClaimVerificationVerdict, ContentFormat
from app.domain.models import Claim, FactCheckReport, ResearchDossier, ResearchSource, Scene, Script
from app.media.director.models import (
    BeatPurpose,
    EvidenceBinding,
    NarrativeBeat,
    VisualIntent,
    VisualModality,
)
from app.media.director.storyboard_planner import StoryboardPlanner
from app.media.renderers.evidence_renderer import EvidenceRenderer


def test_evidence_does_not_quote_narration_as_source_excerpt(tmp_path: Path):
    """Ensure non-verbatim claims do NOT render quotation marks or pretend to be document quotations."""
    renderer = EvidenceRenderer(width=1080, height=1920)
    out_path = tmp_path / "card_paraphrase.png"

    # Render paraphrase / non-verbatim claim
    p, h = renderer.render_evidence_card(
        source_title="PostgreSQL 16 Documentation",
        source_url="https://postgresql.org/docs/16/wal.html",
        highlighted_claim="Write-ahead logging ensures durability by writing changes before data pages.",
        output_path=out_path,
        is_verbatim=False,
        claim_verified=True,
    )
    assert Path(p).exists()
    assert Path(p).stat().st_size > 0

    # Render verbatim excerpt
    verbatim_out = tmp_path / "card_verbatim.png"
    pv, hv = renderer.render_evidence_card(
        source_title="PostgreSQL 16 Documentation",
        source_url="https://postgresql.org/docs/16/wal.html",
        highlighted_claim="WAL is write ahead log.",
        output_path=verbatim_out,
        is_verbatim=True,
        claim_verified=True,
    )
    assert Path(pv).exists()
    assert h != hv  # Distinct visual output


def test_evidence_binding_uses_verified_claim_source():
    """Verify that StoryboardPlanner binds evidence shots to verified claims and real dossier sources."""
    planner = StoryboardPlanner()

    source = ResearchSource(
        id="src_pg_wal",
        title="PostgreSQL WAL Architecture",
        url="https://postgresql.org/docs/wal",
        content_sha256="abc123hash",
    )
    claim = Claim(
        id="clm_01",
        source_id="src_pg_wal",
        statement="PostgreSQL WAL buffers sequential writes to disk before memory flushing.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
        cited_url="https://postgresql.org/docs/wal",
        cited_excerpt="The WAL buffers sequential writes to disk prior to buffer page sync.",
    )
    dossier = ResearchDossier(
        id="dos_01",
        topic_id="top_01",
        sources=[source],
        claims=[claim],
        summary="WAL research summary",
    )
    fact_report = FactCheckReport(
        id="fc_01",
        project_id="proj_01",
        claims=[claim],
        verified_count=1,
        failed_count=0,
        audit_summary="All claims verified",
    )

    beat = NarrativeBeat(
        beat_id="b_01",
        scene_index=0,
        narration="PostgreSQL WAL buffers sequential writes to disk before memory flushing.",
        duration_hint=3.0,
        purpose=BeatPurpose.PROVE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        key_claim="PostgreSQL WAL buffers sequential writes to disk before memory flushing.",
        source_refs=["src_pg_wal"],
    )

    binding = planner._resolve_evidence_binding(beat, dossier=dossier, fact_report=fact_report)
    assert binding is not None
    assert binding.claim_id == "clm_01"
    assert binding.source_ref == "src_pg_wal"
    assert binding.source_url == "https://postgresql.org/docs/wal"
    assert binding.claim_verified is True
    assert binding.source_excerpt == "The WAL buffers sequential writes to disk prior to buffer page sync."
    assert binding.excerpt_is_verbatim is True


def test_unverified_evidence_reroutes_to_diagram():
    """If a DOCUMENT_EVIDENCE modality has no matching verified claim or source, reroute to DIAGRAM."""
    planner = StoryboardPlanner()

    script = Script(
        id="scr_01",
        title="WAL Internals",
        hook="How WAL works.",
        scenes=[Scene(index=0, narration="Unverified claim about database engine internals.", target_duration_seconds=3.0)],
        total_word_count=7,
        estimated_duration_seconds=3.0,
        content_format=ContentFormat.EXPLAINER,
    )

    beat = NarrativeBeat(
        beat_id="b_01",
        scene_index=0,
        narration="Unverified claim about database engine internals.",
        duration_hint=3.0,
        purpose=BeatPurpose.PROVE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        key_claim="Unverified claim about database engine internals.",
        source_refs=[],
    )

    # Empty dossier and fact report -> cannot resolve evidence binding
    dossier = ResearchDossier(id="dos_01", topic_id="top_01", sources=[], claims=[], summary="")
    fact_report = FactCheckReport(id="fc_01", project_id="proj_01", claims=[], audit_summary="")

    storyboard = planner.plan_storyboard(
        project_id="proj_01",
        script=script,
        beats=[beat],
        total_audio_duration=3.0,
        dossier=dossier,
        fact_report=fact_report,
    )

    assert len(storyboard.shots) == 1
    # Modality must be safely rerouted to DIAGRAM because evidence cannot be grounded
    assert storyboard.shots[0].visual_modality == VisualModality.DIAGRAM


def test_missing_source_reroutes_to_diagram():
    """Missing source ref or claim must reroute DOCUMENT_EVIDENCE to DIAGRAM."""
    test_unverified_evidence_reroutes_to_diagram()


def test_evidence_with_placeholder_url_is_rejected():
    """Validate that placeholder URLs (internal, localhost, example.com) are rejected by evidence validation."""
    from app.media.director.quality_evaluator import validate_evidence_binding

    for bad_url in [
        "https://verified-source.internal",
        "http://localhost:8000/docs",
        "https://127.0.0.1/evidence",
        "https://example.com/source",
        "placeholder",
        "https://test.internal/claim",
    ]:
        binding = EvidenceBinding(
            claim_id="clm_01",
            source_ref="src_01",
            source_title="Internal Ref",
            source_url=bad_url,
            claim_verified=True,
            source_excerpt="Some excerpt",
            excerpt_is_verbatim=True,
        )
        is_valid, reason = validate_evidence_binding(binding)
        assert is_valid is False
        assert "placeholder" in reason.lower() or "internal" in reason.lower() or "invalid" in reason.lower()


def test_unverified_evidence_binding_fails_creative_qa():
    """Creative QA must fail when an ungrounded or unverified evidence binding is attached to a shot."""
    from app.media.director.models import ShotSpec, Storyboard
    from app.media.director.quality_evaluator import VisualShotEvaluator

    evaluator = VisualShotEvaluator()

    unverified_binding = EvidenceBinding(
        claim_id="clm_fake",
        source_ref="src_fake",
        source_title="Unverified Source",
        source_url="https://valid-domain.org/research",
        claim_verified=False,  # Unverified!
        source_excerpt="Unverified text",
        excerpt_is_verbatim=False,
    )

    shot = ShotSpec(
        shot_id="shot_unverified_ev",
        scene_index=0,
        beat_id="beat_01",
        duration_seconds=3.0,
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=unverified_binding,
        narration_segment="Some narration about facts",
        headline_text="Facts Headline",
    )

    storyboard = Storyboard(
        project_id="proj_ev_test",
        shots=[shot],
        total_duration=3.0,
    )

    report = evaluator.generate_quality_report(storyboard=storyboard)
    assert report.creative_status == "FAIL"
    assert any("UNGROUNDED_EVIDENCE" in issue for issue in report.critical_failures)


def test_verified_claim_with_real_source_passes():
    """Valid verified claim with real non-placeholder source URL must pass Creative QA evidence check."""
    from app.media.director.models import ShotSpec, Storyboard
    from app.media.director.quality_evaluator import VisualShotEvaluator

    evaluator = VisualShotEvaluator()

    valid_binding = EvidenceBinding(
        claim_id="clm_verified",
        source_ref="src_real",
        source_title="PostgreSQL 16 WAL",
        source_url="https://postgresql.org/docs/16/wal.html",
        claim_verified=True,
        source_excerpt="WAL records all changes before writing.",
        excerpt_is_verbatim=True,
    )

    shot = ShotSpec(
        shot_id="shot_verified_ev",
        scene_index=0,
        beat_id="beat_01",
        duration_seconds=3.0,
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=valid_binding,
        narration_segment="PostgreSQL ensures durability via WAL.",
        headline_text="WAL Durability",
    )

    storyboard = Storyboard(
        project_id="proj_ev_test",
        shots=[shot],
        total_duration=3.0,
    )

    report = evaluator.generate_quality_report(storyboard=storyboard)
    assert not any("UNGROUNDED_EVIDENCE" in issue for issue in report.critical_failures)

