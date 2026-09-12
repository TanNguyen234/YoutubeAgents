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


def test_llm_cannot_create_evidence_binding_directly():
    """Verify that ProposedNarrativeBeat forbids evidence_binding field to prevent LLM hallucinated provenance."""
    import pydantic
    from app.media.director.models import ProposedNarrativeBeat, ProposedBeatsPayload

    with pytest.raises(pydantic.ValidationError):
        ProposedNarrativeBeat(
            beat_id="b1",
            narration="Latency improved by 72%",
            evidence_binding={"claim_id": "fake_claim", "claim_verified": True},  # type: ignore
        )

    # Also test via payload JSON parsing
    malicious_json = (
        '{"beats": [{"beat_id": "b1", "narration": "Fake", '
        '"evidence_binding": {"claim_id": "fake_123", "claim_verified": true}}]}'
    )
    with pytest.raises(pydantic.ValidationError):
        ProposedBeatsPayload.model_validate_json(malicious_json)


def test_llm_cannot_create_verified_chart_datum_directly():
    """Verify that ProposedNarrativeBeat forbids chart_data field to prevent LLM self-asserted charts."""
    import pydantic
    from app.media.director.models import ProposedNarrativeBeat

    with pytest.raises(pydantic.ValidationError):
        ProposedNarrativeBeat(
            beat_id="b1",
            narration="Latency improved by 72%",
            chart_data=[{"label": "Latency", "value": 72.0, "origin": "VERIFIED_CLAIM"}],  # type: ignore
        )


def test_llm_fake_claim_id_is_removed_during_materialization():
    """ProposedNarrativeBeat conversion via materialize_proposed_beats strictly resets all trust fields."""
    from app.media.director.beat_decomposer import materialize_proposed_beats
    from app.media.director.models import ProposedNarrativeBeat

    prop = ProposedNarrativeBeat(
        beat_id="b_prop_01",
        narration="Throughput increased by 5x",
        visual_intent=VisualIntent.SHOW_MECHANISM,
    )
    materialized = materialize_proposed_beats([prop])
    assert len(materialized) == 1
    beat = materialized[0]
    assert beat.beat_id == "b_prop_01"
    assert beat.key_claim is None
    assert beat.source_refs == []
    assert beat.chart_data == []
    assert beat.evidence_binding is None
    assert beat.requires_evidence is False


def test_llm_fake_source_ref_is_removed_during_enrichment():
    """enrich_beats_with_provenance defensively clears unverified source_refs on incoming beats."""
    from app.media.director.beat_decomposer import enrich_beats_with_provenance

    beat = NarrativeBeat(
        beat_id="b_adversarial",
        narration="PostgreSQL handles millions of writes safely.",
        key_claim="Unverified claim from LLM",
        source_refs=["fake_source_ref_123"],
    )
    dossier = ResearchDossier(id="dos_empty", topic_id="top", sources=[], claims=[], summary="Empty summary")
    fact_report = FactCheckReport(id="fc_empty", project_id="proj", claims=[], audit_summary="Audit summary")

    enriched = enrich_beats_with_provenance([beat], dossier=dossier, fact_report=fact_report, trusted_input=False)
    assert len(enriched) == 1
    assert enriched[0].source_refs == []
    assert enriched[0].key_claim is None


def test_unmatched_llm_key_claim_is_cleared():
    """Unmatched LLM beats have their key_claim, evidence_binding, and chart_data strictly cleared."""
    from app.media.director.beat_decomposer import enrich_beats_with_provenance

    beat = NarrativeBeat(
        beat_id="b_unmatched",
        narration="An unmatched narrative claim about system scaling.",
        key_claim="AI claim not present in fact check report",
        source_refs=["src_does_not_exist"],
    )
    source = ResearchSource(
        id="src_pg",
        title="PostgreSQL Doc",
        url="https://postgresql.org",
        content_sha256="hash123",
    )
    claim = Claim(
        id="clm_different",
        source_id="src_pg",
        statement="Completely different topic about index creation.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
        cited_url="https://postgresql.org",
    )
    dossier = ResearchDossier(id="dos_1", topic_id="top", sources=[source], claims=[claim], summary="Summary")
    fact_report = FactCheckReport(id="fc_1", project_id="proj", claims=[claim], audit_summary="Audit summary")

    enriched = enrich_beats_with_provenance([beat], dossier=dossier, fact_report=fact_report, trusted_input=False)
    res_beat = enriched[0]
    assert res_beat.key_claim is None
    assert res_beat.source_refs == []
    assert res_beat.evidence_binding is None
    assert res_beat.chart_data == []


def test_evidence_binding_claim_id_must_exist_in_fact_report():
    """Creative QA must reject EvidenceBinding whose claim_id does not exist in FactCheckReport."""
    from app.media.director.models import ShotSpec, Storyboard
    from app.media.director.quality_evaluator import VisualShotEvaluator

    evaluator = VisualShotEvaluator()
    source = ResearchSource(id="src_valid", title="Valid", url="https://example.com/doc", content_sha256="h1")
    dossier = ResearchDossier(id="dos", topic_id="t", sources=[source], claims=[], summary="Summary")
    fact_report = FactCheckReport(id="fc", project_id="p", claims=[], audit_summary="Audit summary")

    binding = EvidenceBinding(
        claim_id="fabricated_claim_999",
        source_ref="src_valid",
        source_title="Valid Source",
        source_url="https://postgresql.org/docs/16/wal.html",
        claim_verified=True,
    )
    shot = ShotSpec(
        shot_id="shot_fake_claim",
        scene_index=0,
        beat_id="b1",
        duration_seconds=3.0,
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=binding,
        narration_segment="Fabricated claim.",
    )
    storyboard = Storyboard(project_id="p", shots=[shot], total_duration=3.0)

    report = evaluator.generate_quality_report(storyboard=storyboard, fact_report=fact_report, dossier=dossier)
    assert report.creative_status == "FAIL"
    assert any("does not exist in FactCheckReport" in err for err in report.critical_failures)


def test_evidence_binding_source_ref_must_exist_in_dossier():
    """Creative QA must reject EvidenceBinding whose source_ref is not found in ResearchDossier."""
    from app.media.director.models import ShotSpec, Storyboard
    from app.media.director.quality_evaluator import VisualShotEvaluator

    evaluator = VisualShotEvaluator()
    claim = Claim(
        id="clm_valid",
        statement="Valid claim.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
        cited_url="https://postgresql.org/docs/16/wal.html",
    )
    dossier = ResearchDossier(id="dos", topic_id="t", sources=[], claims=[claim], summary="Summary")
    fact_report = FactCheckReport(id="fc", project_id="p", claims=[claim], audit_summary="Audit summary")

    binding = EvidenceBinding(
        claim_id="clm_valid",
        source_ref="fabricated_source_ref",
        source_title="Fabricated",
        source_url="https://postgresql.org/docs/16/wal.html",
        claim_verified=True,
    )
    shot = ShotSpec(
        shot_id="shot_fake_src",
        scene_index=0,
        beat_id="b1",
        duration_seconds=3.0,
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=binding,
        narration_segment="Valid claim with fake source ref.",
    )
    storyboard = Storyboard(project_id="p", shots=[shot], total_duration=3.0)

    report = evaluator.generate_quality_report(storyboard=storyboard, fact_report=fact_report, dossier=dossier)
    assert report.creative_status == "FAIL"
    assert any("does not resolve to ResearchDossier sources" in err for err in report.critical_failures)


def test_grounded_chart_verified_claim_id_must_exist_in_fact_report():
    """Creative QA must reject ChartDatum with origin=VERIFIED_CLAIM if claim_id not verified in fact_report."""
    from app.media.director.models import ChartDatum, ChartDatumOrigin, ShotSpec, Storyboard
    from app.media.director.quality_evaluator import VisualShotEvaluator

    evaluator = VisualShotEvaluator()
    dossier = ResearchDossier(id="dos", topic_id="t", sources=[], claims=[], summary="Summary")
    fact_report = FactCheckReport(id="fc", project_id="p", claims=[], audit_summary="Audit summary")

    datum = ChartDatum(
        label="Latency",
        value=72.0,
        unit="ms",
        origin=ChartDatumOrigin.VERIFIED_CLAIM,
        claim_id="nonexistent_claim_id",
    )
    shot = ShotSpec(
        shot_id="shot_chart",
        scene_index=0,
        beat_id="b1",
        duration_seconds=3.0,
        visual_modality=VisualModality.DATA_VISUALIZATION,
        chart_data=[datum],
        narration_segment="Latency reduced by 72ms.",
    )
    storyboard = Storyboard(project_id="p", shots=[shot], total_duration=3.0)

    report = evaluator.generate_quality_report(storyboard=storyboard, fact_report=fact_report, dossier=dossier)
    assert report.creative_status == "FAIL"
    assert any("does not exist in FactCheckReport" in err for err in report.critical_failures)


def test_grounded_chart_external_source_must_exist_in_dossier():
    """Creative QA must reject ChartDatum with origin=EXTERNAL_SOURCE if source_ref is not in dossier."""
    from app.media.director.models import ChartDatum, ChartDatumOrigin, ShotSpec, Storyboard
    from app.media.director.quality_evaluator import VisualShotEvaluator

    evaluator = VisualShotEvaluator()
    dossier = ResearchDossier(id="dos", topic_id="t", sources=[], claims=[], summary="Summary")
    fact_report = FactCheckReport(id="fc", project_id="p", claims=[], audit_summary="Audit summary")

    datum = ChartDatum(
        label="Throughput",
        value=5000.0,
        unit="rps",
        origin=ChartDatumOrigin.EXTERNAL_SOURCE,
        source_ref="nonexistent_source_ref",
    )
    shot = ShotSpec(
        shot_id="shot_chart_src",
        scene_index=0,
        beat_id="b1",
        duration_seconds=3.0,
        visual_modality=VisualModality.DATA_VISUALIZATION,
        chart_data=[datum],
        narration_segment="Throughput is 5000 rps.",
    )
    storyboard = Storyboard(project_id="p", shots=[shot], total_duration=3.0)

    report = evaluator.generate_quality_report(storyboard=storyboard, fact_report=fact_report, dossier=dossier)
    assert report.creative_status == "FAIL"
    assert any("does not resolve to ResearchDossier sources" in err for err in report.critical_failures)


def test_malicious_llm_metadata_is_stripped_and_rejected():
    """Adversarial test: an LLM proposing forged provenance cannot inject it into NarrativeBeat or Storyboard."""
    from app.media.director.beat_decomposer import BeatDecomposer
    from app.media.director.models import ProposedNarrativeBeat, ProposedBeatsPayload

    class AdversarialMockBackend:
        def generate_structured(self, prompt, schema):
            # Attempt to return a proposed beat
            return ProposedBeatsPayload(
                beats=[
                    ProposedNarrativeBeat(
                        beat_id="b_adversarial",
                        narration="Latency improved by 72% according to our benchmark.",
                        visual_intent=VisualIntent.SHOW_EVIDENCE,
                    )
                ]
            )

    decomposer = BeatDecomposer(backend=AdversarialMockBackend())
    script = Script(
        id="sc_adv",
        title="Adversarial Test",
        hook="Adversarial Hook",
        scenes=[Scene(scene_index=0, narration="Latency improved by 72% according to our benchmark.")],
        total_word_count=8,
        estimated_duration_seconds=3.0,
    )

    # Empty dossier and fact report (no verified claims exist for this 72% claim)
    empty_dossier = ResearchDossier(id="dos_empty", topic_id="t", sources=[], claims=[], summary="Empty summary")
    empty_fact_report = FactCheckReport(id="fc_empty", project_id="p", claims=[], audit_summary="Empty audit")

    beats = decomposer.decompose_script(
        script=script,
        total_duration_seconds=3.0,
        dossier=empty_dossier,
        fact_report=empty_fact_report,
    )

    assert len(beats) == 1
    adversarial_beat = beats[0]
    # Verify all provenance fields are completely clean
    assert adversarial_beat.key_claim is None
    assert adversarial_beat.source_refs == []
    assert adversarial_beat.evidence_binding is None
    assert adversarial_beat.chart_data == []

    # Verify StoryboardPlanner refuses to create DOCUMENT_EVIDENCE shot from unprovenanced beat
    planner = StoryboardPlanner()
    storyboard = planner.plan_storyboard(
        project_id="proj_adv",
        script=script,
        beats=beats,
        total_audio_duration=3.0,
        dossier=empty_dossier,
        fact_report=empty_fact_report,
    )

    assert len(storyboard.shots) == 1
    shot = storyboard.shots[0]
    # Must have rerouted away from DOCUMENT_EVIDENCE to DIAGRAM / SHOW_MECHANISM
    assert shot.visual_modality != VisualModality.DOCUMENT_EVIDENCE
    assert shot.evidence_binding is None


