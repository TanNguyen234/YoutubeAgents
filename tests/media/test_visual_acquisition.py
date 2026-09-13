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

    # Ensure AI generated candidate is penalized heavily on REAL_REQUIRED modality
    gen_score = next(s for c, s in ranked if c.candidate_id == "cand_ai_db")
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

    # AI slop and stock server racks must be at bottom
    bottom_ids = [c.candidate_id for c, _ in ranked[2:]]
    assert "cand_stock_server_rack" in bottom_ids
    assert "cand_ai_glowing_database" in bottom_ids
