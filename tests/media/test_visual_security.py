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

    # Matching domain or prefix passes
    valid_exact, _ = validate_capture_url("https://sqlite.org/wal.html", mode="EVIDENCE", trusted_urls=trusted)
    assert valid_exact

    valid_subpage, _ = validate_capture_url("https://sqlite.org/pragma.html", mode="EVIDENCE", trusted_urls=trusted)
    assert valid_subpage

    # Untrusted domain fails
    untrusted, reason = validate_capture_url("https://malicious-site.com/exploit", mode="EVIDENCE", trusted_urls=trusted)
    assert not untrusted
    assert "UNTRUSTED_SOURCE_URL" in reason
