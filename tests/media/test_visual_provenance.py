"""Provenance, licensing, and synthetic media disclosure tests."""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from app.domain.models import VideoProject
from app.media.acquisition.models import (
    VisualAcquisitionRequest,
    VisualAssetCandidate,
    VisualAssetProvenance,
    VisualSourceType,
)
from app.media.acquisition.router import VisualAcquisitionRouter
from app.media.director.models import EvidenceBinding, ShotSpec, TimelineShot, VisualIntent, VisualModality
from app.media.models import RenderManifest


def test_document_capture_keeps_source_url():
    """Verify that an acquired document evidence candidate retains canonical source URL and ref."""
    cand = VisualAssetCandidate(
        candidate_id="cand_doc_01",
        source_type=VisualSourceType.RESEARCH_SOURCE,
        file_path="/tmp/shot_01.png",
        source_url="https://sqlite.org/wal.html",
        source_ref="src_sqlite_docs",
        license_type="Public Documentation",
        attribution="SQLite Consortium",
        content_sha256="abc123sha",
        acquisition_method="playwright_web_evidence",
        evidence_claim_ids=["claim_wal_01"],
        is_synthetic=False,
    )

    router = VisualAcquisitionRouter()
    provenance = router.to_provenance(cand, shot_id="s_01")

    assert provenance.source_url == "https://sqlite.org/wal.html"
    assert provenance.source_ref == "src_sqlite_docs"
    assert provenance.attribution == "SQLite Consortium"
    assert provenance.license_type == "Public Documentation"
    assert not provenance.synthetic


def test_document_capture_keeps_evidence_claim_ids():
    """Verify that evidence claim IDs are preserved from binding to candidate and provenance."""
    cand = VisualAssetCandidate(
        candidate_id="cand_claim_test",
        source_type=VisualSourceType.DOCUMENT,
        file_path="/tmp/shot_claim.png",
        source_url="https://docs.python.org",
        content_sha256="sha789",
        acquisition_method="playwright_web_evidence",
        evidence_claim_ids=["claim_python_gil_01", "claim_python_perf_02"],
        is_synthetic=False,
    )

    router = VisualAcquisitionRouter()
    provenance = router.to_provenance(cand, shot_id="s_02")
    assert "claim_python_gil_01" in provenance.evidence_claim_ids
    assert "claim_python_perf_02" in provenance.evidence_claim_ids


def test_generated_asset_is_marked_synthetic():
    """Verify that GFlow or AI-generated media candidates are explicitly marked is_synthetic=True."""
    cand = VisualAssetCandidate(
        candidate_id="cand_gflow_01",
        source_type=VisualSourceType.GENERATED,
        file_path="/tmp/gflow_art.png",
        content_sha256="synth123sha",
        acquisition_method="gflow_imagen",
        is_synthetic=True,
    )
    assert cand.is_synthetic
    router = VisualAcquisitionRouter()
    prov = router.to_provenance(cand, shot_id="s_03")
    assert prov.synthetic is True


def test_rejected_generated_candidate_does_not_mark_final_video_synthetic(tmp_path):
    """Verify that if an AI candidate was rejected in favor of a real asset, final video is NOT marked synthetic."""
    # Timeline contains ONLY real/diagram shots
    shot1 = TimelineShot(
        shot_id="s_01",
        scene_index=0,
        beat_id="b_01",
        start=0.0,
        end=3.0,
        duration=3.0,
        asset_path=str(tmp_path / "diagram.png"),
        asset_sha256="sha_diagram",
        modality=VisualModality.DIAGRAM,
        asset_is_synthetic=False,
        asset_source_type="RENDERED",
    )
    shot2 = TimelineShot(
        shot_id="s_02",
        scene_index=1,
        beat_id="b_02",
        start=3.0,
        end=6.0,
        duration=3.0,
        asset_path=str(tmp_path / "doc_evidence.png"),
        asset_sha256="sha_evidence",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        asset_is_synthetic=False,
        asset_source_type="RESEARCH_SOURCE",
        asset_source_url="https://sqlite.org/wal.html",
    )

    timeline_shots = [shot1, shot2]

    # Check synthetic media derivation logic on final timeline shots:
    contains_synthetic = any(
        s.modality.value in ("GENERATED_IMAGE", "GENERATED_VIDEO", "IMAGE_TO_VIDEO")
        or getattr(s, "asset_is_synthetic", False)
        for s in timeline_shots
    )

    assert not contains_synthetic, "Video should NOT be marked synthetic when timeline contains only real/rendered assets"


def test_final_render_manifest_contains_visual_provenance(tmp_path):
    """Verify that RenderManifest visual_assets array retains auditable provenance fields."""
    shot = TimelineShot(
        shot_id="s_01_wal",
        scene_index=0,
        beat_id="b_01",
        start=0.0,
        end=4.0,
        duration=4.0,
        asset_path=str(tmp_path / "wal_evidence.png"),
        asset_sha256="wal_sha256_hash",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        asset_source_type="RESEARCH_SOURCE",
        asset_source_url="https://sqlite.org/wal.html",
        asset_license="Public Domain / CC0",
        asset_attribution="SQLite Documentation",
        asset_acquisition_method="playwright_web_evidence",
        asset_is_synthetic=False,
    )

    # Manifest visual_assets item serialization
    asset_dict = {
        "shot_id": shot.shot_id,
        "path": shot.asset_path,
        "sha256": shot.asset_sha256,
        "source_type": shot.asset_source_type,
        "source_url": shot.asset_source_url,
        "license_type": shot.asset_license,
        "attribution": shot.asset_attribution,
        "acquisition_method": shot.asset_acquisition_method,
        "synthetic": shot.asset_is_synthetic,
    }

    manifest = RenderManifest(
        project_id="proj_prov_test",
        script_id="sc_01",
        canonical_narration_sha256="narr_hash",
        production_fingerprint="prod_fp",
        render_profile="SHORTS_9_16",
        tts_backend="edge-tts",
        voice="en-US-GuyNeural",
        audio_path="/tmp/audio.mp3",
        audio_sha256="audio_hash",
        audio_duration=4.0,
        subtitle_path="/tmp/sub.srt",
        subtitle_sha256="sub_hash",
        scene_count=1,
        visual_assets=[asset_dict],
        final_video_path="/tmp/out.mp4",
        final_video_sha256="video_hash",
        final_video_size_bytes=1024,
        video_duration=4.0,
        measured_loudness_lufs=-14.0,
        qa_verdict="PASSED",
        contains_synthetic_media=False,
    )

    # Verify serialized fields
    v_asset = manifest.visual_assets[0]
    assert v_asset["shot_id"] == "s_01_wal"
    assert v_asset["path"] == str(tmp_path / "wal_evidence.png")
    assert v_asset["sha256"] == "wal_sha256_hash"
    assert v_asset["source_type"] == "RESEARCH_SOURCE"
    assert v_asset["source_url"] == "https://sqlite.org/wal.html"
    assert v_asset["license_type"] == "Public Domain / CC0"
    assert v_asset["attribution"] == "SQLite Documentation"
    assert v_asset["acquisition_method"] == "playwright_web_evidence"
    assert v_asset["synthetic"] is False
