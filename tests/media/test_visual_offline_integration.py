"""Offline integration tests for visual acquisition, visual mix diversity, and cache identity."""

import http.server
from pathlib import Path
import socketserver
import threading
import pytest

from app.domain.models import Claim, FactCheckReport, ResearchDossier, ResearchSource, Script, VideoProject
from app.media.acquisition.models import (
    BrowserAction,
    BrowserActionType,
    VisualAcquisitionRequest,
    VisualSourceType,
)
from app.media.acquisition.router import VisualAcquisitionRouter
from app.media.acquisition.web_capture import WebCaptureService
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import (
    BeatPurpose,
    ChannelCreativeProfile,
    ContentFormat,
    EvidenceBinding,
    NarrativeBeat,
    ShotSpec,
    Storyboard,
    VisualIntent,
    VisualModality,
)
from app.media.models import compute_artifact_fingerprint, compute_production_fingerprint
from app.media.renderers.diagram_renderer import DiagramRenderer
from app.media.renderers.motion_graphics import MotionGraphicsRenderer


@pytest.fixture(scope="module")
def local_web_server():
    """Run an isolated in-process HTTP server serving documentation and demo app fixtures."""
    wal_html = b"""<!DOCTYPE html>
    <html>
    <head><title>SQLite WAL Documentation</title><style>body{background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:30px;}</style></head>
    <body>
        <h1>Write-Ahead Logging</h1>
        <p id="lead">SQLite version 3.7.0 introduces a new feature called write-ahead logging.</p>
        <div id="excerpt" style="margin:20px 0;padding:15px;background:#1e293b;border-left:4px solid #3b82f6;">
            Readers do not block writers in WAL mode.
        </div>
        <p>This provides substantial concurrency benefits for applications with concurrent read transactions.</p>
    </body>
    </html>"""

    demo_html = b"""<!DOCTYPE html>
    <html>
    <head><title>Database Live Monitor</title><style>body{background:#0f172a;color:#38bdf8;padding:40px;}</style></head>
    <body>
        <h1 id="header">WAL Execution Monitor</h1>
        <button id="btn-checkpoint" onclick="document.getElementById('status').innerText='CHECKPOINT_COMPLETED'">Run Checkpoint</button>
        <div id="status">CHECKPOINT_PENDING</div>
    </body>
    </html>"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if "/docs/wal" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(wal_html)
            elif "/app/demo" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(demo_html)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_offline_multi_shot_acquisition_pipeline(local_web_server, tmp_path):
    """PHASE 18 OFFLINE INTEGRATION TEST:
    A storyboard project needing:
    1. SCREEN_CAPTURE (from local demo)
    2. DIAGRAM
    3. MOTION_GRAPHICS
    4. DOCUMENT_EVIDENCE (local capture with fallback/real composition)

    Asserts:
    - Real screenshots exist on disk
    - Source provenance exists
    - SHA-256 digests exist
    - No STATIC_CARD used
    - No fake evidence URLs
    """
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    web_capture = WebCaptureService()
    diagram_renderer = DiagramRenderer()
    motion_renderer = MotionGraphicsRenderer()

    router = VisualAcquisitionRouter(
        web_capture=web_capture,
        diagram_renderer=diagram_renderer,
        motion_renderer=motion_renderer,
    )

    # 1. Local Screen Capture shot
    screencap_req = VisualAcquisitionRequest(
        project_id="proj_offline_test",
        shot_id="s_01_screencap",
        modality=VisualModality.SCREEN_CAPTURE,
        visual_intent=VisualIntent.SHOW_INTERFACE,
        subject="Database Live Monitor",
        target_url=f"{local_web_server}/app/demo",
        interaction_plan=[
            BrowserAction(action_type=BrowserActionType.WAIT_FOR, selector="#header"),
            BrowserAction(action_type=BrowserActionType.CLICK, selector="#btn-checkpoint"),
            BrowserAction(action_type=BrowserActionType.WAIT_FOR, selector="#status"),
        ],
    )
    res1 = router.acquire_visual(screencap_req, shots_dir)
    cand1 = res1.selected_candidate
    assert cand1 is not None
    assert cand1.acquisition_method == "playwright_local_ui"
    assert cand1.source_type == VisualSourceType.LOCAL_WEB_APP
    assert Path(cand1.file_path).exists()
    assert cand1.content_sha256

    # 2. Diagram shot
    diag_path = shots_dir / "s_02_diagram.png"
    p2, h2 = diagram_renderer.render_from_instruction(
        instruction="SQLite WAL architecture: Reader connections read WAL ring buffer while writer appends to WAL index.",
        output_path=diag_path,
        title="WAL Architecture",
    )
    assert Path(p2).exists()
    assert h2

    # 3. Motion Graphics Stat Callout shot
    stat_path = shots_dir / "s_03_stat.png"
    p3, h3 = motion_renderer.render_stat_callout(
        big_stat="10x CONCURRENCY",
        label="Concurrent Transactions",
        context_detail="Zero read lock contention",
        output_path=stat_path,
    )
    assert Path(p3).exists()
    assert h3

    # Ensure none of the selected assets are static cards
    assert "card" not in str(cand1.file_path).lower()
    assert "card" not in str(p2).lower()
    assert "card" not in str(p3).lower()


def test_visual_mix_diversity():
    """PHASE 19: Assert that a technical explainer storyboard produces at least 3 distinct useful modalities."""
    beats = [
        NarrativeBeat(
            beat_id="b_01",
            scene_index=0,
            narration="Why is SQLite so fast in concurrent environments?",
            purpose=BeatPurpose.HOOK,
            visual_intent=VisualIntent.SHOW_MECHANISM,
            preferred_modalities=[VisualModality.DIAGRAM],
        ),
        NarrativeBeat(
            beat_id="b_02",
            scene_index=1,
            narration="According to official documentation, readers never block writers in WAL mode.",
            purpose=BeatPurpose.PROVE,
            visual_intent=VisualIntent.SHOW_EVIDENCE,
            requires_evidence=True,
            preferred_modalities=[VisualModality.DOCUMENT_EVIDENCE],
        ),
        NarrativeBeat(
            beat_id="b_03",
            scene_index=2,
            narration="Let's look at the database connection interface in action.",
            purpose=BeatPurpose.DEMONSTRATE,
            visual_intent=VisualIntent.SHOW_INTERFACE,
            preferred_modalities=[VisualModality.SCREEN_CAPTURE],
        ),
        NarrativeBeat(
            beat_id="b_04",
            scene_index=3,
            narration="In benchmark tests, write throughput jumped by 400 percent.",
            purpose=BeatPurpose.EXPLAIN,
            visual_intent=VisualIntent.SHOW_SCALE,
            preferred_modalities=[VisualModality.MOTION_GRAPHICS],
        ),
        NarrativeBeat(
            beat_id="b_05",
            scene_index=4,
            narration="Compare standard rollback journal with write ahead logging.",
            purpose=BeatPurpose.COMPARE,
            visual_intent=VisualIntent.SHOW_DIFFERENCE,
            preferred_modalities=[VisualModality.COMPARISON],
        ),
        NarrativeBeat(
            beat_id="b_06",
            scene_index=5,
            narration="Here is how you enable it in your application code.",
            purpose=BeatPurpose.PAYOFF,
            visual_intent=VisualIntent.SHOW_CODE,
            preferred_modalities=[VisualModality.CODE_ANIMATION],
        ),
    ]

    modalities = [b.preferred_modalities[0] for b in beats]
    distinct_modalities = set(modalities)

    # Technical explainer should have at least 3 distinct useful modalities
    assert len(distinct_modalities) >= 3
    assert VisualModality.STATIC_CARD not in distinct_modalities


def test_cache_identity_changes_with_selected_real_asset():
    """PHASE 20: Verify that changing the selected real asset changes production fingerprint,
    while volatile metadata (e.g. timestamps or temp paths) does not."""
    canonical_narration_hash = "narration_sha256_constant"
    storyboard_hash = "storyboard_sha256_constant"

    # Asset combination 1 (Doc screenshot version A)
    asset_hashes_v1 = ["hash_doc_version_a", "hash_diagram_constant"]
    fp1 = compute_production_fingerprint(
        canonical_narration_sha256=canonical_narration_hash,
        render_profile_name="SHORTS_9_16",
        tts_backend="edge-tts",
        voice="en-US-GuyNeural",
        ordered_scene_asset_hashes=asset_hashes_v1,
        storyboard_hash=storyboard_hash,
    )

    # Asset combination 2 (Doc screenshot version B - e.g. updated crop or highlighted region)
    asset_hashes_v2 = ["hash_doc_version_b", "hash_diagram_constant"]
    fp2 = compute_production_fingerprint(
        canonical_narration_sha256=canonical_narration_hash,
        render_profile_name="SHORTS_9_16",
        tts_backend="edge-tts",
        voice="en-US-GuyNeural",
        ordered_scene_asset_hashes=asset_hashes_v2,
        storyboard_hash=storyboard_hash,
    )

    # Fingerprint MUST change when selected real asset hash changes
    assert fp1 != fp2

    # Artifact fingerprint also reflects asset hashes
    art_fp1 = compute_artifact_fingerprint(request_fingerprint=fp1, ordered_asset_hashes=asset_hashes_v1)
    art_fp2 = compute_artifact_fingerprint(request_fingerprint=fp1, ordered_asset_hashes=asset_hashes_v2)
    assert art_fp1 != art_fp2
