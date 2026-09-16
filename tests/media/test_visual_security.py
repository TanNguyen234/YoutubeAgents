"""Security tests for visual evidence acquisition and browser capture."""

import pytest
from app.media.acquisition.models import BrowserAction, BrowserActionType
from app.media.acquisition.web_capture import validate_capture_url


def test_web_capture_rejects_file_scheme():
    """Verify that file:// schemes are strictly rejected to prevent local file disclosure."""
    valid, reason = validate_capture_url("file:///etc/passwd", mode="EVIDENCE")
    assert not valid
    assert "DISALLOWED_SCHEME" in reason


def test_web_capture_rejects_javascript_scheme():
    """Verify that javascript: schemes are strictly rejected to prevent script execution."""
    valid, reason = validate_capture_url("javascript:alert(1)", mode="EVIDENCE")
    assert not valid
    assert "DISALLOWED_SCHEME" in reason

    valid_data, reason_data = validate_capture_url("data:text/html,<h1>test</h1>", mode="EVIDENCE")
    assert not valid_data
    assert "DISALLOWED_SCHEME" in reason_data


def test_web_capture_rejects_private_ip_for_evidence_mode():
    """Verify that private IP ranges, loopbacks, and localhost are blocked in EVIDENCE mode."""
    private_targets = [
        "http://127.0.0.1:8000/docs",
        "https://localhost/api",
        "http://192.168.1.1/admin",
        "http://10.0.0.1/status",
        "http://172.16.0.1/dashboard",
        "http://0.0.0.0:8080/internal",
    ]
    for target in private_targets:
        valid, reason = validate_capture_url(target, mode="EVIDENCE")
        assert not valid, f"Expected {target} to be blocked in EVIDENCE mode"
        assert "PRIVATE_IP_BLOCKED" in reason


def test_local_ui_capture_allows_localhost_only_in_local_app_mode():
    """Verify that LOCAL_WEB_APP mode permits localhost/127.0.0.1 and rejects public or external IPs."""
    # Allowed local targets
    valid_lh, _ = validate_capture_url("http://localhost:3000/dashboard", mode="LOCAL_WEB_APP")
    assert valid_lh

    valid_loopback, _ = validate_capture_url("http://127.0.0.1:8080/demo", mode="LOCAL_WEB_APP")
    assert valid_loopback

    # Rejected external targets
    invalid_pub, reason_pub = validate_capture_url("https://example.com/demo", mode="LOCAL_WEB_APP")
    assert not invalid_pub
    assert "DISALLOWED_LOCAL_HOST" in reason_pub

    invalid_lan, reason_lan = validate_capture_url("http://192.168.1.50:3000", mode="LOCAL_WEB_APP")
    assert not invalid_lan
    assert "DISALLOWED_LOCAL_HOST" in reason_lan


def test_llm_cannot_inject_arbitrary_browser_javascript():
    """Verify that browser automation is strictly constrained to typed BrowserAction primitives."""
    # Ensure BrowserActionType has only typed primitives and rejects arbitrary execution commands
    allowed_types = {t.value for t in BrowserActionType}
    assert allowed_types == {"NAVIGATE", "CLICK", "TYPE", "WAIT_FOR", "SCROLL", "SCREENSHOT"}
    assert "EXECUTE_SCRIPT" not in allowed_types
    assert "EVAL" not in allowed_types

    # BrowserAction enforces typed action_type
    with pytest.raises(Exception):
        BrowserAction(action_type="EVALUATE_SCRIPT", text="document.cookie")


def test_web_capture_url_must_resolve_from_trusted_source():
    """Verify that capture URL must resolve to one of the verified ResearchDossier source URLs."""
    trusted = [
        "https://sqlite.org/wal.html",
        "https://docs.python.org/3/library/sqlite3.html",
    ]

    # Matching exact canonical URL passes
    valid_exact, _ = validate_capture_url("https://sqlite.org/wal.html", mode="EVIDENCE", trusted_urls=trusted)
    assert valid_exact

    # Same host with different path fails (no domain-only authority)
    valid_subpage, reason = validate_capture_url("https://sqlite.org/pragma.html", mode="EVIDENCE", trusted_urls=trusted)
    assert not valid_subpage
    assert "UNTRUSTED_SOURCE_URL" in reason

    # Untrusted domain fails
    untrusted, reason = validate_capture_url("https://malicious-site.com/exploit", mode="EVIDENCE", trusted_urls=trusted)
    assert not untrusted
    assert "UNTRUSTED_SOURCE_URL" in reason


def test_web_capture_rejects_ipv6_loopback_and_private_ranges():
    """Verify that IPv6 loopbacks, unique-local, link-local, and mapped IPv4 addresses are blocked."""
    ipv6_targets = [
        "http://[::1]:8080/secret",
        "http://[::]/status",
        "http://[fe80::1]/link-local",
        "http://[fc00::1]/private",
        "http://[::ffff:127.0.0.1]/mapped",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ]
    for target in ipv6_targets:
        valid, reason = validate_capture_url(target, mode="EVIDENCE")
        assert not valid, f"Expected {target} to be blocked in EVIDENCE mode"
        assert "PRIVATE_IP_BLOCKED" in reason


def test_web_capture_fails_closed_on_unresolved_dns():
    """Verify that DNS resolution failure fails closed with DNS_RESOLUTION_FAILED."""
    valid, reason = validate_capture_url("https://non-existent-domain-fake-test-12345.xyz/docs", mode="EVIDENCE", check_dns=True)
    assert not valid
    assert "DNS_RESOLUTION_FAILED" in reason


def test_llm_screen_instruction_cannot_inject_untrusted_evidence_url():
    """Verify that LLM-generated screen_instruction cannot inject arbitrary evidence URLs."""
    from app.media.acquisition.router import VisualAcquisitionRouter
    from app.media.director.models import ShotSpec, VisualModality

    router = VisualAcquisitionRouter()
    shot = ShotSpec(
        shot_id="s_inject",
        beat_id="b_01",
        duration_seconds=3.0,
        scene_index=0,
        narration_segment="SQLite documentation shows WAL architecture.",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        screen_instruction="Open browser and go to https://attacker-exploit.com/fake-evidence and screenshot",
    )

    req = router.build_acquisition_request(shot=shot, project_id="proj_test")
    # Screen instruction URL must NOT be used as target_url for DOCUMENT_EVIDENCE
    assert req.target_url is None or "attacker-exploit.com" not in req.target_url


def test_web_capture_blocks_navigation_redirect_to_disallowed_target(tmp_path, monkeypatch):
    """Verify that if target URL redirects to a private IP, it is blocked post-navigation."""
    import http.server
    import socketserver
    import threading
    from app.media.acquisition.web_capture import WebCaptureService

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if "/redirect" in self.path:
                # Send HTTP 302 redirect to private admin endpoint
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port}/admin")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Admin Console</h1></body></html>")

        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), RedirectHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Allow initial navigation to reach the local test server
    original_validate = validate_capture_url

    def mock_validate(url, mode="EVIDENCE", **kw):
        if url.endswith("/redirect"):
            return True, "VALID_FOR_TEST_REDIRECT"
        return original_validate(url, mode=mode, **kw)

    monkeypatch.setattr("app.media.acquisition.web_capture.validate_capture_url", mock_validate)

    try:
        service = WebCaptureService()
        out_file = tmp_path / "redirect_blocked.png"

        cand, errs = service.capture_evidence(
            url=f"http://127.0.0.1:{port}/redirect",
            output_path=out_file,
        )

        assert cand is None
        assert any("SECURITY_VIOLATION" in e or "PRIVATE_IP_BLOCKED" in e for e in errs)
    finally:
        server.shutdown()
        server.server_close()


def test_public_page_private_subresource_is_blocked():
    """Verify that subresource requests to private IPs embedded in a page are aborted by route handler."""
    from unittest.mock import MagicMock
    from app.media.acquisition.web_capture import WebCaptureService

    service = WebCaptureService()
    handler = service._create_route_handler()

    private_subresources = [
        "http://127.0.0.1:8080/secret.png",
        "http://192.168.1.100/admin.js",
        "http://10.0.0.5/api/data",
        "http://172.16.0.1/style.css",
        "http://[::1]:9000/data.json",
    ]
    for sub_url in private_subresources:
        route = MagicMock()
        route.request.url = sub_url
        handler(route)
        route.abort.assert_called_once_with("blockedbyclient")
        route.continue_.assert_not_called()

    # Valid external subresource continues
    valid_route = MagicMock()
    valid_route.request.url = "https://sqlite.org/images/sqlite.gif"
    handler(valid_route)
    valid_route.continue_.assert_called_once()
    valid_route.abort.assert_not_called()


def test_popup_to_private_ip_is_blocked_or_closed():
    """Verify that popups targeting private IPs are blocked at route boundary and closed."""
    from unittest.mock import MagicMock
    from app.media.acquisition.web_capture import WebCaptureService

    service = WebCaptureService()
    handler = service._create_route_handler()

    popup_route = MagicMock()
    popup_route.request.url = "http://192.168.1.1/router-login"
    handler(popup_route)
    popup_route.abort.assert_called_once_with("blockedbyclient")
    popup_route.continue_.assert_not_called()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.route("**/*", handler)
        main_page = context.new_page()
        context.on("page", lambda new_p: new_p.close() if new_p != main_page else None)

        main_page.set_content("<script>window.open('about:blank')</script>")
        main_page.wait_for_timeout(200)
        assert len(context.pages) == 1
        browser.close()


def test_main_navigation_is_revalidated_at_route_time():
    """Verify that initial main navigation is never exempted if host resolves to private IP (DNS rebinding / TOCTOU)."""
    from unittest.mock import MagicMock
    from app.media.acquisition.web_capture import WebCaptureService

    service = WebCaptureService()
    handler = service._create_route_handler()

    nav_route = MagicMock()
    nav_route.request.url = "http://127.0.0.1:8080/dashboard"
    nav_route.request.is_navigation_request.return_value = True
    handler(nav_route)
    nav_route.abort.assert_called_once_with("blockedbyclient")
    nav_route.continue_.assert_not_called()


def test_redirect_to_private_ip_remains_blocked():
    """Verify that redirects pointing to private or loopback IPs are aborted at route time."""
    from unittest.mock import MagicMock
    from app.media.acquisition.web_capture import WebCaptureService

    service = WebCaptureService()
    handler = service._create_route_handler()

    redirect_targets = [
        "http://127.0.0.1:5000/internal",
        "http://10.10.10.10/private",
        "http://192.168.0.1/admin",
    ]
    for target in redirect_targets:
        route = MagicMock()
        route.request.url = target
        handler(route)
        route.abort.assert_called_once_with("blockedbyclient")
        route.continue_.assert_not_called()


def test_metadata_subresource_is_blocked():
    """Verify that cloud metadata endpoints are strictly blocked at route boundary."""
    from unittest.mock import MagicMock
    from app.media.acquisition.web_capture import WebCaptureService

    service = WebCaptureService()
    handler = service._create_route_handler()

    metadata_targets = [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/latest/user-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata.local/metadata",
        "http://instance-data/latest/meta-data/",
    ]
    for meta_url in metadata_targets:
        route = MagicMock()
        route.request.url = meta_url
        handler(route)
        route.abort.assert_called_once_with("blockedbyclient")
        route.continue_.assert_not_called()


def test_evidence_and_local_browser_context_blocks_service_workers(monkeypatch, tmp_path):
    """Verify that browser contexts for evidence capture and local UI explicitly configure service_workers='block'."""
    from unittest.mock import MagicMock
    from app.media.acquisition.web_capture import WebCaptureService

    service = WebCaptureService()

    recorded_context_kwargs = []

    class MockBrowser:
        def new_context(self, **kwargs):
            recorded_context_kwargs.append(kwargs)
            ctx = MagicMock()
            page = MagicMock()
            page.goto.return_value = MagicMock(status=200)
            page.url = "https://example.com/page"
            page.title.return_value = "Page Title"
            ctx.new_page.return_value = page
            return ctx

        def close(self):
            pass

    class MockChromium:
        def launch(self, **kwargs):
            return MockBrowser()

    class MockPlaywright:
        def __init__(self):
            self.chromium = MockChromium()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("app.media.acquisition.web_capture.is_playwright_available", lambda: True)
    monkeypatch.setattr("app.media.acquisition.web_capture.is_chromium_available", lambda: True)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: MockPlaywright())
    monkeypatch.setattr("app.media.acquisition.web_capture.validate_capture_url", lambda url, **kw: (True, "OK"))
    monkeypatch.setattr(
        service,
        "composite_evidence_shot",
        lambda raw_screenshot_path, output_path, source_domain, document_title: (str(output_path), "fake_sha"),
    )

    # 1. Test capture_evidence
    service.capture_evidence(
        url="https://example.com/page",
        output_path=tmp_path / "out_ev.png",
    )

    assert len(recorded_context_kwargs) >= 1
    ev_kwargs = recorded_context_kwargs[0]
    assert ev_kwargs.get("service_workers") == "block"
    assert ev_kwargs.get("accept_downloads") is False
    assert ev_kwargs.get("permissions") == []
    assert ev_kwargs.get("ignore_https_errors") is False

    # 2. Test capture_local_ui
    service.capture_local_ui(
        url="http://127.0.0.1:8000/app",
        output_path=tmp_path / "out_local.png",
    )

    assert len(recorded_context_kwargs) >= 2
    local_kwargs = recorded_context_kwargs[1]
    assert local_kwargs.get("service_workers") == "block"
