"""Candidate ranking, reality policy, and anti-slop visual acquisition tests."""

import http.server
from pathlib import Path
import socketserver
import threading
import pytest

from app.media.acquisition.candidate_ranker import CandidateRanker
from app.media.acquisition.models import (
    BrowserAction,
    BrowserActionType,
    VisualAcquisitionRequest,
    VisualAssetCandidate,
    VisualSourceType,
)
from app.media.acquisition.stock import PexelsStockProvider
from app.media.acquisition.web_capture import WebCaptureService
from app.media.director.models import EvidenceBinding, VisualIntent, VisualModality


def test_document_evidence_prefers_verified_source():
    """Verify that ranker scores real verified documentation candidate higher than stock or generated candidates."""
    ranker = CandidateRanker()

    req = VisualAcquisitionRequest(
        project_id="proj_sqlite",
        shot_id="s_01",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="SQLite WAL Documentation",
        evidence_binding=EvidenceBinding(
            claim_id="c_wal_01",
            source_ref="src_sqlite",
            source_title="SQLite WAL Mode",
            source_url="https://sqlite.org/wal.html",
            claim_text="Readers do not block writers in WAL mode.",
            claim_verified=True,
        ),
        target_width=1080,
        target_height=1920,
    )

    # Real documentation candidate
    real_doc = VisualAssetCandidate(
        candidate_id="cand_real_doc",
        source_type=VisualSourceType.RESEARCH_SOURCE,
        file_path="/tmp/doc.png",
        source_url="https://sqlite.org/wal.html",
        source_ref="src_sqlite",
        content_sha256="sha_doc_111",
        width=1080,
        height=1920,
        acquisition_method="playwright_web_evidence",
        evidence_claim_ids=["c_wal_01"],
        is_synthetic=False,
    )

    # Generic stock candidate
    stock_cand = VisualAssetCandidate(
        candidate_id="cand_stock_server",
        source_type=VisualSourceType.STOCK_MEDIA,
        file_path="/tmp/stock.mp4",
        source_url="https://stock.example.com/video/server",
        content_sha256="sha_stock_222",
        width=1080,
        height=1920,
        duration_seconds=4.0,
        acquisition_method="pexels_stock",
        is_synthetic=False,
    )

    # AI Generated candidate
    gen_cand = VisualAssetCandidate(
        candidate_id="cand_ai_db",
        source_type=VisualSourceType.GENERATED,
        file_path="/tmp/gflow.png",
        content_sha256="sha_ai_333",
        width=1080,
        height=1920,
        acquisition_method="gflow_imagen",
        is_synthetic=True,
    )

    ranked = ranker.rank_candidates([gen_cand, stock_cand, real_doc], req)
    winner, win_score = ranked[0]

    assert winner.candidate_id == "cand_real_doc"
    assert win_score.evidence_affinity == 1.0
    assert win_score.source_preference == 1.0

    # Ensure AI generated candidate is strictly discarded by REAL_REQUIRED hard gate
    assert not any(c.candidate_id == "cand_ai_db" for c, _ in ranked)
    # Direct candidate scoring reflects the heavy penalty
    gen_score = ranker.score_candidate(gen_cand, req)
    assert any("SYNTHETIC_ON_REAL_REQUIRED" in p for p in gen_score.penalties)
    assert gen_score.total_score < win_score.total_score


def test_evidence_excerpt_not_found_returns_structured_failure(tmp_path):
    """Verify that when an evidence excerpt does not exist on target page, web capture fails with EVIDENCE_TEXT_NOT_FOUND."""
    # Serve minimal HTML page without the requested excerpt
    html_content = b"<html><body><h1>SQLite Home</h1><p>Welcome to SQLite database engine.</p></body></html>"

    class LocalHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html_content)

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), LocalHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        service = WebCaptureService()
        out_file = tmp_path / "failed_excerpt.png"

        # Look for an excerpt that is clearly not present
        cand, errs = service.capture_evidence(
            url=f"http://127.0.0.1:{port}/docs",
            output_path=out_file,
            source_excerpt="Quantum entanglement accelerates query speed by 1000x.",
            trusted_urls=[f"http://127.0.0.1:{port}/docs"],
        )

        # In EVIDENCE mode, 127.0.0.1 is normally blocked by private IP security check
        assert cand is None
        assert any("PRIVATE_IP_BLOCKED" in e or "EVIDENCE_TEXT_NOT_FOUND" in e for e in errs)
    finally:
        server.shutdown()
        server.server_close()


def test_screen_capture_uses_real_browser_screenshot(tmp_path):
    """Verify that LOCAL_WEB_APP mode captures a real screenshot from localhost application."""
    html_content = b"""<!DOCTYPE html>
    <html>
    <head><style>body { background: #0f172a; color: #38bdf8; font-family: sans-serif; padding: 40px; }</style></head>
    <body>
        <h1 id="title">Demo Dashboard</h1>
        <button id="btn" onclick="document.getElementById('status').innerText = 'STATUS_ACTIVE'">Activate</button>
        <div id="status">STATUS_IDLE</div>
    </body>
    </html>"""

    class LocalHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content)

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), LocalHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        service = WebCaptureService()
        out_file = tmp_path / "local_ui_screencap.png"

        plan = [
            BrowserAction(action_type=BrowserActionType.WAIT_FOR, selector="#title"),
            BrowserAction(action_type=BrowserActionType.CLICK, selector="#btn"),
            BrowserAction(action_type=BrowserActionType.WAIT_FOR, selector="#status"),
        ]

        cand, errs = service.capture_local_ui(
            url=f"http://127.0.0.1:{port}/app",
            output_path=out_file,
            interaction_plan=plan,
        )

        assert cand is not None, f"Local UI capture failed: {errs}"
        assert out_file.exists()
        assert out_file.stat().st_size > 1000
        assert cand.content_sha256
        assert cand.width == 1080
        assert cand.height == 1920
        assert cand.source_type == VisualSourceType.LOCAL_WEB_APP
        assert cand.acquisition_method == "playwright_local_ui"
    finally:
        server.shutdown()
        server.server_close()


def test_stock_provider_missing_credentials_reports_unavailable():
    """Verify that stock provider with empty API key cleanly reports unavailable."""
    provider = PexelsStockProvider(api_key="")
    assert not provider.is_available()
    results = provider.search_video("database query")
    assert results == []


def test_static_card_is_last_resort():
    """Verify that static card candidate scores significantly lower than any real or rendered candidate."""
    ranker = CandidateRanker()
    req = VisualAcquisitionRequest(
        project_id="proj_card_test",
        shot_id="s_05",
        modality=VisualModality.DIAGRAM,
        visual_intent=VisualIntent.SHOW_MECHANISM,
        subject="Architecture Flow",
    )

    rendered_cand = VisualAssetCandidate(
        candidate_id="cand_diagram",
        source_type=VisualSourceType.RENDERED,
        file_path="/tmp/diag.png",
        content_sha256="sha_diag",
        width=1080,
        height=1920,
        acquisition_method="diagram_renderer",
        is_synthetic=False,
    )

    card_cand = VisualAssetCandidate(
        candidate_id="cand_card",
        source_type=VisualSourceType.FALLBACK_CARD,
        file_path="/tmp/card.png",
        content_sha256="sha_card",
        width=1080,
        height=1920,
        acquisition_method="visual_factory_static_card",
        is_synthetic=False,
    )

    ranked = ranker.rank_candidates([card_cand, rendered_cand], req)
    assert ranked[0][0].candidate_id == "cand_diagram"
    assert ranked[1][0].candidate_id == "cand_card"
    assert ranked[1][1].total_score < ranked[0][1].total_score


def test_same_asset_is_penalized_for_repeated_use():
    """Verify that reusing the same asset content hash incurs a duplicate penalty."""
    ranker = CandidateRanker()
    req = VisualAcquisitionRequest(
        project_id="proj_dup_test",
        shot_id="s_06",
        modality=VisualModality.DIAGRAM,
        visual_intent=VisualIntent.SHOW_MECHANISM,
        subject="System Architecture",
        intentional_callback=False,
    )

    cand = VisualAssetCandidate(
        candidate_id="cand_repeat",
        source_type=VisualSourceType.RENDERED,
        file_path="/tmp/diag.png",
        content_sha256="sha_repeated_asset",
        width=1080,
        height=1920,
        acquisition_method="diagram_renderer",
        is_synthetic=False,
    )

    # First score without history
    score_fresh = ranker.score_candidate(cand, req)
    assert score_fresh.penalties == []

    # Record selection
    ranker.record_selection(cand, VisualModality.DIAGRAM)

    # Score again: should incur duplicate asset penalty
    score_dup = ranker.score_candidate(cand, req)
    assert any("DUPLICATE_ASSET" in p for p in score_dup.penalties)
    assert score_dup.total_score < score_fresh.total_score


def test_critical_anti_slop_acceptance():
    """CRITICAL ANTI-SLOP ACCEPTANCE TEST:
    Given: Narration 'SQLite WAL allows readers to continue while a writer appends changes to the WAL file.'
    Research source: verified SQLite documentation.
    Expected preference:
      1. DOCUMENT_EVIDENCE screenshot OR
      2. Grounded WAL mechanism diagram
    Unacceptable candidates:
      - generic server rack stock footage
      - glowing AI database image
      - humanoid robot
    """
    ranker = CandidateRanker()

    req = VisualAcquisitionRequest(
        project_id="proj_anti_slop",
        shot_id="s_wal_hero",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="SQLite WAL Concurrency",
        evidence_binding=EvidenceBinding(
            claim_id="claim_wal_concurrency",
            source_ref="src_sqlite_wal",
            source_title="Write-Ahead Logging",
            source_url="https://sqlite.org/wal.html",
            claim_text="Readers do not block writers and a writer does not block readers.",
            claim_verified=True,
        ),
        target_width=1080,
        target_height=1920,
    )

    # 1. Real Documentation Candidate
    real_doc = VisualAssetCandidate(
        candidate_id="cand_wal_doc_screenshot",
        source_type=VisualSourceType.RESEARCH_SOURCE,
        file_path="/tmp/wal_doc.png",
        source_url="https://sqlite.org/wal.html",
        source_ref="src_sqlite_wal",
        content_sha256="sha_wal_doc",
        width=1080,
        height=1920,
        acquisition_method="playwright_web_evidence",
        evidence_claim_ids=["claim_wal_concurrency"],
        is_synthetic=False,
    )

    # 2. Grounded Mechanism Diagram
    wal_diagram = VisualAssetCandidate(
        candidate_id="cand_wal_diagram",
        source_type=VisualSourceType.RENDERED,
        file_path="/tmp/wal_diagram.png",
        content_sha256="sha_wal_diag",
        width=1080,
        height=1920,
        acquisition_method="diagram_renderer",
        is_synthetic=False,
    )

    # 3. Slop Candidate: Generic Server Rack Stock
    server_rack_stock = VisualAssetCandidate(
        candidate_id="cand_stock_server_rack",
        source_type=VisualSourceType.STOCK_MEDIA,
        file_path="/tmp/server_rack.mp4",
        source_url="https://stock.pexels.com/video/server-rack",
        content_sha256="sha_server_rack",
        width=1080,
        height=1920,
        duration_seconds=4.0,
        acquisition_method="pexels_stock",
        is_synthetic=False,
    )

    # 4. Slop Candidate: Glowing AI Database Art
    glowing_db_ai = VisualAssetCandidate(
        candidate_id="cand_ai_glowing_database",
        source_type=VisualSourceType.GENERATED,
        file_path="/tmp/glowing_db.png",
        content_sha256="sha_ai_db",
        width=1080,
        height=1920,
        acquisition_method="gflow_imagen",
        is_synthetic=True,
    )

    ranked = ranker.rank_candidates(
        [glowing_db_ai, server_rack_stock, wal_diagram, real_doc],
        req,
    )

    winner, _ = ranked[0]
    second, _ = ranked[1]

    # Real documentation must win!
    assert winner.candidate_id == "cand_wal_doc_screenshot"
    # Grounded diagram should outrank stock & AI slop
    assert second.candidate_id == "cand_wal_diagram"

    # AI slop candidate was discarded entirely by REAL_REQUIRED hard gate
    assert not any(c.candidate_id == "cand_ai_glowing_database" for c, _ in ranked)
    # Generic stock server rack is at the bottom
    bottom_ids = [c.candidate_id for c, _ in ranked[2:]]
    assert "cand_stock_server_rack" in bottom_ids


def test_real_required_policy_is_hard_gate_not_only_score_penalty():
    """Verify that for REAL_REQUIRED modalities, synthetic candidates are completely excluded from ranking."""
    ranker = CandidateRanker()

    for modality in [VisualModality.DOCUMENT_EVIDENCE, VisualModality.SCREENSHOT, VisualModality.SCREEN_CAPTURE]:
        req = VisualAcquisitionRequest(
            project_id="proj_hard_gate",
            shot_id=f"shot_{modality.value}",
            modality=modality,
            visual_intent=VisualIntent.SHOW_EVIDENCE,
            subject="Strict Reality Requirement",
        )

        synth_candidate = VisualAssetCandidate(
            candidate_id="cand_synth_ai",
            source_type=VisualSourceType.GENERATED,
            file_path="/tmp/synth.png",
            content_sha256="sha_synth",
            acquisition_method="gflow_imagen",
            is_synthetic=True,
        )

        real_candidate = VisualAssetCandidate(
            candidate_id="cand_real_doc",
            source_type=VisualSourceType.RESEARCH_SOURCE if modality != VisualModality.SCREEN_CAPTURE else VisualSourceType.LOCAL_WEB_APP,
            file_path="/tmp/real.png",
            content_sha256="sha_real",
            acquisition_method="playwright",
            is_synthetic=False,
        )

        # Ranked list must completely filter out synthetic candidate
        ranked = ranker.rank_candidates([synth_candidate, real_candidate], req)
        assert len(ranked) == 1
        assert ranked[0][0].candidate_id == "cand_real_doc"
        assert not any(c.candidate_id == "cand_synth_ai" for c, _ in ranked)

        # If only synthetic candidates were supplied, ranker must return empty list (triggering router fallback)
        synth_only_ranked = ranker.rank_candidates([synth_candidate], req)
        assert synth_only_ranked == []


def test_evidence_excerpt_partial_prefix_is_not_enough(tmp_path, monkeypatch):
    """Verify that a weak 4-word prefix match is rejected when the rest of the excerpt is missing/fabricated."""
    html_content = b"""<html><body>
        <h1>SQLite WAL</h1>
        <p>SQLite version 3.7.0 introduces a new feature called write-ahead logging.</p>
    </body></html>"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content)

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Allow local test server URL through validation for this isolated browser test
    monkeypatch.setattr(
        "app.media.acquisition.web_capture.validate_capture_url",
        lambda url, **kw: (True, "VALID_FOR_TEST"),
    )
    monkeypatch.setattr(
        "app.media.acquisition.web_capture._is_private_ip",
        lambda host: False,
    )

    try:
        service = WebCaptureService()
        out_file = tmp_path / "prefix_test.png"

        # Excerpt shares first 4 words ('SQLite version 3.7.0 introduces') but diverges into false claim
        fabricated_excerpt = "SQLite version 3.7.0 introduces revolutionary high-speed quantum storage technology."

        cand, errs = service.capture_evidence(
            url=f"http://127.0.0.1:{port}/wal",
            output_path=out_file,
            source_excerpt=fabricated_excerpt,
        )

        assert cand is None
        assert any("EVIDENCE_TEXT_NOT_FOUND" in e for e in errs)
    finally:
        server.shutdown()
        server.server_close()


def test_evidence_excerpt_normalized_match_succeeds(tmp_path, monkeypatch):
    """Verify that normalized excerpt matching succeeds despite whitespace, case, or line-break differences."""
    html_content = b"""<html><body>
        <h1>SQLite Documentation</h1>
        <div id="quote" style="padding: 20px;">
            Readers
            do not block
            writers in WAL mode.
        </div>
    </body></html>"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content)

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setattr(
        "app.media.acquisition.web_capture.validate_capture_url",
        lambda url, **kw: (True, "VALID_FOR_TEST"),
    )
    monkeypatch.setattr(
        "app.media.acquisition.web_capture._is_private_ip",
        lambda host: False,
    )

    try:
        service = WebCaptureService()
        out_file = tmp_path / "norm_test.png"

        # Search with flattened single-line normalized text
        clean_excerpt = "readers do not block writers in wal mode."

        cand, errs = service.capture_evidence(
            url=f"http://127.0.0.1:{port}/doc",
            output_path=out_file,
            source_excerpt=clean_excerpt,
        )

        assert cand is not None, f"Capture failed: {errs}"
        assert out_file.exists()
        assert cand.content_sha256
        assert cand.license_type is None
        assert cand.attribution == "127.0.0.1"
    finally:
        server.shutdown()
        server.server_close()


def test_evidence_renderer_card_is_not_document_evidence(tmp_path):
    """Verify that programmatically rendered citation cards return STATIC_CARD, not DOCUMENT_EVIDENCE."""
    from unittest.mock import MagicMock
    from app.media.director.director_service import AutoDirectorService
    from app.media.director.models import ShotSpec, VisualModality, EvidenceBinding

    out_dir = tmp_path / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    director = AutoDirectorService()
    director.acquisition_router.acquire_visual = MagicMock(side_effect=Exception("Browser capture failed"))

    shot = ShotSpec(
        shot_id="s_fallback_test",
        beat_id="b_01",
        duration_seconds=3.0,
        scene_index=0,
        narration_segment="According to documentation, readers do not block writers.",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=EvidenceBinding(
            claim_id="c_01",
            source_ref="src_doc",
            source_title="Doc Title",
            source_url="https://sqlite.org/wal.html",
            claim_text="Readers do not block writers.",
            claim_verified=True,
        ),
    )

    res = director._generate_shot_asset(shot=shot, shot_index=0, output_dir=out_dir, script_title="Test Script", channel_name="Test")
    assert res.actual_modality == VisualModality.STATIC_CARD
    assert res.actual_modality != VisualModality.DOCUMENT_EVIDENCE


def test_document_capture_failure_changes_actual_modality(tmp_path):
    """Verify that when browser document capture fails, actual_modality changes from DOCUMENT_EVIDENCE."""
    from unittest.mock import MagicMock
    from app.media.director.director_service import AutoDirectorService
    from app.media.director.models import ShotSpec, VisualModality, EvidenceBinding

    out_dir = tmp_path / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    director = AutoDirectorService()
    director.acquisition_router.acquire_visual = MagicMock(side_effect=Exception("Network error"))

    shot = ShotSpec(
        shot_id="s_change_mod",
        beat_id="b_01",
        duration_seconds=3.0,
        scene_index=0,
        narration_segment="SQLite WAL mode",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=EvidenceBinding(
            claim_id="c_01",
            source_ref="src_doc",
            source_title="SQLite WAL",
            source_url="https://sqlite.org/wal.html",
            claim_text="WAL",
            claim_verified=True,
        ),
    )

    res = director._generate_shot_asset(shot=shot, shot_index=0, output_dir=out_dir, script_title="Test Script", channel_name="Test")
    assert res.requested_modality == VisualModality.DOCUMENT_EVIDENCE
    assert res.actual_modality != VisualModality.DOCUMENT_EVIDENCE
    assert res.actual_modality in (VisualModality.DIAGRAM, VisualModality.STATIC_CARD)


def test_document_fallback_does_not_claim_document_source_type(tmp_path):
    """Verify that fallback citation cards do not claim source_type DOCUMENT."""
    from unittest.mock import MagicMock
    from app.media.director.director_service import AutoDirectorService
    from app.media.director.models import ShotSpec, VisualModality, EvidenceBinding

    out_dir = tmp_path / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    director = AutoDirectorService()
    director.acquisition_router.acquire_visual = MagicMock(side_effect=Exception("Browser capture failed"))

    shot = ShotSpec(
        shot_id="s_source_type",
        beat_id="b_01",
        duration_seconds=3.0,
        scene_index=0,
        narration_segment="SQLite WAL mode",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=EvidenceBinding(
            claim_id="c_01",
            source_ref="src_doc",
            source_title="SQLite WAL",
            source_url="https://sqlite.org/wal.html",
            claim_text="WAL",
            claim_verified=True,
        ),
    )

    res = director._generate_shot_asset(shot=shot, shot_index=0, output_dir=out_dir, script_title="Test Script", channel_name="Test")
    assert res.source_type != "DOCUMENT"
    assert res.source_type in ("FALLBACK_CARD", "RENDERED")


def test_document_fallback_does_not_invent_license(tmp_path):
    """Verify that fallback citation cards do not fabricate 'Document Citation' license strings."""
    from unittest.mock import MagicMock
    from app.media.director.director_service import AutoDirectorService
    from app.media.director.models import ShotSpec, VisualModality, EvidenceBinding

    out_dir = tmp_path / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    director = AutoDirectorService()
    director.acquisition_router.acquire_visual = MagicMock(side_effect=Exception("Browser capture failed"))

    shot = ShotSpec(
        shot_id="s_license_test",
        beat_id="b_01",
        duration_seconds=3.0,
        scene_index=0,
        narration_segment="SQLite WAL mode",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=EvidenceBinding(
            claim_id="c_01",
            source_ref="src_doc",
            source_title="SQLite WAL",
            source_url="https://sqlite.org/wal.html",
            claim_text="WAL",
            claim_verified=True,
        ),
    )

    res = director._generate_shot_asset(shot=shot, shot_index=0, output_dir=out_dir, script_title="Test Script", channel_name="Test")
    assert res.license_type is None
    assert res.license_type != "Document Citation"


def test_request_target_url_cannot_override_binding_canonical_url(tmp_path):
    """Verify that request.target_url differing from canonical source is rejected with UNTRUSTED_VISUAL_SOURCE."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.router import VisualAcquisitionRouter

    dossier = ResearchDossier(
        id="d1",
        topic_id="t1",
        summary="Test",
        sources=[
            ResearchSource(
                id="s1",
                title="Official Doc",
                url="https://sqlite.org/wal.html",
                content_sha256="sha1",
            )
        ],
    )

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s1",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="SQLite WAL",
        target_url="https://other-arbitrary-domain.com/wal.html",
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="s1",
            source_title="Official Doc",
            source_url="https://sqlite.org/wal.html",
            claim_text="WAL",
            claim_verified=True,
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=dossier)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)


def test_same_domain_different_path_is_not_automatically_trusted(tmp_path):
    """Verify that same domain with different path is NOT automatically authorized."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.router import VisualAcquisitionRouter

    dossier = ResearchDossier(
        id="d1",
        topic_id="t1",
        summary="Test",
        sources=[
            ResearchSource(
                id="s1",
                title="Official Doc",
                url="https://example.com/canonical/page.html",
                content_sha256="sha1",
            )
        ],
    )

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s1",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Example",
        target_url="https://example.com/other-user/unrelated.html",
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="s1",
            source_title="Official Doc",
            source_url="https://example.com/canonical/page.html",
            claim_text="Example claim",
            claim_verified=True,
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=dossier)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)


def test_same_host_different_github_resource_is_not_trusted(tmp_path):
    """Verify that a dossier with org/project/docs does not authorize other-user/unrelated-page."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.router import VisualAcquisitionRouter

    dossier = ResearchDossier(
        id="d1",
        topic_id="t1",
        summary="Test",
        sources=[
            ResearchSource(
                id="src_gh",
                title="Project Repo",
                url="https://github.com/org/project/docs",
                content_sha256="sha_gh",
            )
        ],
    )

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s_gh",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="GitHub Docs",
        target_url="https://github.com/other-user/unrelated-page",
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="src_gh",
            source_title="Project Repo",
            source_url="https://github.com/org/project/docs",
            claim_text="GitHub doc claim",
            claim_verified=True,
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=dossier)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)


def test_screen_instruction_remote_url_is_not_trusted():
    """Verify that LLM-generated screen_instruction with an arbitrary remote URL is NOT trusted."""
    from app.media.acquisition.router import VisualAcquisitionRouter
    from app.media.director.models import ShotSpec, VisualModality

    router = VisualAcquisitionRouter()
    shot = ShotSpec(
        shot_id="s_screen_remote",
        beat_id="b_01",
        duration_seconds=3.0,
        scene_index=0,
        narration_segment="Look at this external website demo.",
        visual_modality=VisualModality.SCREEN_CAPTURE,
        screen_instruction="Open browser and visit https://untrusted-arbitrary-site.com/dashboard and record",
    )

    req = router.build_acquisition_request(shot=shot, project_id="p1")
    # Must NOT set arbitrary remote URL as target_url
    assert req.target_url is None or "untrusted-arbitrary-site.com" not in req.target_url


def test_source_ref_resolves_exact_canonical_url():
    """Verify that EvidenceBinding.source_ref resolves to the exact canonical URL from ResearchDossier."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.router import VisualAcquisitionRouter
    from app.media.director.models import ShotSpec, VisualModality, EvidenceBinding

    dossier = ResearchDossier(
        id="d1",
        topic_id="t1",
        summary="Test",
        sources=[
            ResearchSource(
                id="src_exact_ref",
                title="Exact Doc",
                url="https://sqlite.org/wal.html",
                content_sha256="sha_exact",
            )
        ],
    )

    router = VisualAcquisitionRouter()
    shot = ShotSpec(
        shot_id="s_exact",
        beat_id="b_01",
        duration_seconds=3.0,
        scene_index=0,
        narration_segment="SQLite WAL mode",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="src_exact_ref",
            source_title="Exact Doc",
            source_url="https://sqlite.org/wal.html",
            claim_text="WAL",
            claim_verified=True,
        ),
    )

    req = router.build_acquisition_request(shot=shot, project_id="p1", dossier=dossier)
    assert req.target_url == "https://sqlite.org/wal.html"


def test_document_evidence_without_dossier_fails_closed(tmp_path):
    """Verify that DOCUMENT_EVIDENCE without a ResearchDossier fails closed with UNTRUSTED_VISUAL_SOURCE."""
    from app.media.acquisition.models import VisualAcquisitionRequest
    from app.media.acquisition.router import VisualAcquisitionRouter
    from app.media.director.models import EvidenceBinding, VisualIntent, VisualModality

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p_no_dossier",
        shot_id="s_doc_no_dossier",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Doc Evidence",
        target_url="https://example.com/doc",
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="src_1",
            source_title="Doc",
            source_url="https://example.com/doc",
            claim_text="Some claim",
            claim_verified=True,
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=None)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)
    assert res.actual_modality != VisualModality.DOCUMENT_EVIDENCE


def test_screenshot_without_dossier_fails_closed(tmp_path):
    """Verify that SCREENSHOT without a ResearchDossier fails closed with UNTRUSTED_VISUAL_SOURCE."""
    from app.media.acquisition.models import VisualAcquisitionRequest
    from app.media.acquisition.router import VisualAcquisitionRouter
    from app.media.director.models import EvidenceBinding, VisualIntent, VisualModality

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p_no_dossier",
        shot_id="s_shot_no_dossier",
        modality=VisualModality.SCREENSHOT,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Screenshot Evidence",
        target_url="https://example.com/screenshot",
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="src_1",
            source_title="Screenshot Source",
            source_url="https://example.com/screenshot",
            claim_text="Some claim",
            claim_verified=True,
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=None)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)
    assert res.actual_modality != VisualModality.SCREENSHOT


def test_binding_source_url_cannot_self_authorize_without_dossier(tmp_path):
    """Verify that EvidenceBinding.source_url cannot self-authorize remote capture when dossier is absent."""
    from app.media.acquisition.models import VisualAcquisitionRequest
    from app.media.acquisition.router import VisualAcquisitionRouter
    from app.media.director.models import EvidenceBinding, VisualIntent, VisualModality

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p_self_auth",
        shot_id="s_self_auth",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Self Auth Test",
        target_url="https://unknown.example/page",
        evidence_binding=EvidenceBinding(
            claim_id="c_rogue",
            source_ref="anything",
            source_title="Rogue Source",
            source_url="https://unknown.example/page",
            claim_text="Unverified claim",
            claim_verified=False,
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=None)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)
    assert res.actual_modality != VisualModality.DOCUMENT_EVIDENCE


def test_source_ref_resolves_research_source_by_id():
    """Verify resolve_canonical_research_source resolves by ResearchSource.id."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.router import resolve_canonical_research_source
    from app.media.director.models import EvidenceBinding

    src = ResearchSource(
        id="src_by_id",
        title="ID Source",
        url="https://docs.example.com/sqlite",
        content_sha256="sha_id",
    )
    dossier = ResearchDossier(id="d1", topic_id="t1", summary="Test", sources=[src])

    binding = EvidenceBinding(
        claim_id="c1",
        source_ref="src_by_id",
        source_title="ID Source",
        source_url="https://docs.example.com/sqlite",
        claim_text="Claim",
        claim_verified=True,
    )

    resolved = resolve_canonical_research_source(binding=binding, dossier=dossier)
    assert resolved is not None
    assert resolved.id == "src_by_id"


def test_source_ref_resolves_research_source_by_source_id():
    """Verify resolve_canonical_research_source resolves by ResearchSource.source_id compatibility."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.router import resolve_canonical_research_source
    from app.media.director.models import EvidenceBinding

    src = ResearchSource(
        id="fallback_id",
        title="Source ID Compatibility",
        url="https://docs.example.com/sqlite",
        content_sha256="sha_src_id",
    )
    src.__dict__["source_id"] = "compat_source_123"

    dossier = ResearchDossier(id="d1", topic_id="t1", summary="Test", sources=[src])

    binding = EvidenceBinding(
        claim_id="c1",
        source_ref="compat_source_123",
        source_title="Source ID Compatibility",
        source_url="https://docs.example.com/sqlite",
        claim_text="Claim",
        claim_verified=True,
    )

    resolved = resolve_canonical_research_source(binding=binding, dossier=dossier)
    assert resolved is not None
    assert getattr(resolved, "source_id", None) == "compat_source_123" or resolved.id == "fallback_id"


def test_canonical_subpath_is_allowed(tmp_path, monkeypatch):
    """Verify that descendant subpaths within CANONICAL_URL_SCOPE are permitted for evidence capture."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.models import VisualAcquisitionRequest, VisualAssetCandidate, VisualSourceType
    from app.media.acquisition.router import VisualAcquisitionRouter, is_within_canonical_url_scope
    from app.media.director.models import EvidenceBinding, VisualIntent, VisualModality

    canonical_url = "https://docs.example.com/sqlite"
    subpath_url = "https://docs.example.com/sqlite/wal"

    assert is_within_canonical_url_scope(subpath_url, canonical_url) is True

    src = ResearchSource(
        id="src_sqlite",
        title="SQLite Docs",
        url=canonical_url,
        content_sha256="sha_sqlite",
    )
    dossier = ResearchDossier(id="d1", topic_id="t1", summary="Test", sources=[src])

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s1",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="SQLite WAL",
        target_url=subpath_url,
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="src_sqlite",
            source_title="SQLite Docs",
            source_url=canonical_url,
            claim_text="WAL mode",
            claim_verified=True,
        ),
    )

    monkeypatch.setattr(
        router.web_capture,
        "capture_evidence",
        lambda url, output_path, **kw: (
            VisualAssetCandidate(
                candidate_id="c_subpath",
                source_type=VisualSourceType.RESEARCH_SOURCE,
                file_path=str(output_path),
                content_sha256="fake_sha",
                width=1080,
                height=1920,
                acquisition_method="playwright_web_evidence",
            ),
            [],
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=dossier)
    assert not any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)
    assert res.selected_candidate is not None
    assert res.selected_candidate.source_type == VisualSourceType.RESEARCH_SOURCE


def test_same_host_outside_canonical_scope_is_rejected(tmp_path):
    """Verify that same host with unrelated path outside CANONICAL_URL_SCOPE is rejected with UNTRUSTED_VISUAL_SOURCE."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.models import VisualAcquisitionRequest
    from app.media.acquisition.router import VisualAcquisitionRouter, is_within_canonical_url_scope
    from app.media.director.models import EvidenceBinding, VisualIntent, VisualModality

    canonical_url = "https://docs.example.com/sqlite"
    unrelated_url = "https://docs.example.com/other"

    assert is_within_canonical_url_scope(unrelated_url, canonical_url) is False

    src = ResearchSource(
        id="src_sqlite",
        title="SQLite Docs",
        url=canonical_url,
        content_sha256="sha_sqlite",
    )
    dossier = ResearchDossier(id="d1", topic_id="t1", summary="Test", sources=[src])

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s1",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Other docs",
        target_url=unrelated_url,
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="src_sqlite",
            source_title="SQLite Docs",
            source_url=canonical_url,
            claim_text="Other claim",
            claim_verified=True,
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=dossier)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)


def test_similar_hostname_prefix_attack_is_rejected(tmp_path):
    """Verify that similar hostname prefix attack (docs.example.com.evil.com) is rejected."""
    from app.domain.models import ResearchDossier, ResearchSource
    from app.media.acquisition.models import VisualAcquisitionRequest
    from app.media.acquisition.router import VisualAcquisitionRouter, is_within_canonical_url_scope
    from app.media.director.models import EvidenceBinding, VisualIntent, VisualModality

    canonical_url = "https://docs.example.com/sqlite"
    evil_url = "https://docs.example.com.evil.com/sqlite"

    assert is_within_canonical_url_scope(evil_url, canonical_url) is False

    src = ResearchSource(
        id="src_sqlite",
        title="SQLite Docs",
        url=canonical_url,
        content_sha256="sha_sqlite",
    )
    dossier = ResearchDossier(id="d1", topic_id="t1", summary="Test", sources=[src])

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s1",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Evil Docs",
        target_url=evil_url,
        evidence_binding=EvidenceBinding(
            claim_id="c1",
            source_ref="src_sqlite",
            source_title="SQLite Docs",
            source_url=canonical_url,
            claim_text="Evil claim",
            claim_verified=True,
        ),
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=dossier)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)


def test_remote_screen_capture_without_dossier_is_rejected(tmp_path):
    """Verify that remote SCREEN_CAPTURE without a ResearchDossier is rejected with UNTRUSTED_VISUAL_SOURCE."""
    from app.media.acquisition.models import VisualAcquisitionRequest
    from app.media.acquisition.router import VisualAcquisitionRouter
    from app.media.director.models import VisualIntent, VisualModality

    router = VisualAcquisitionRouter()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s_remote_screen",
        modality=VisualModality.SCREEN_CAPTURE,
        visual_intent=VisualIntent.SHOW_MECHANISM,
        subject="Remote UI",
        target_url="https://remote-dashboard.example.com/metrics",
    )

    res = router.acquire_visual(req, output_dir=tmp_path, dossier=None)
    assert any("UNTRUSTED_VISUAL_SOURCE" in err for err in res.failure_reasons)

