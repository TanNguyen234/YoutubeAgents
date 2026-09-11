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
