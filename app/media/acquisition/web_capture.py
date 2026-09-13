"""Playwright-based web evidence capture and safe local UI capture service."""

import hashlib
import ipaddress
import logging
from pathlib import Path
import re
import socket
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont

from app.media.acquisition.models import (
    BrowserAction,
    BrowserActionType,
    VisualAssetCandidate,
    VisualSourceType,
)

logger = logging.getLogger(__name__)


def is_playwright_available() -> bool:
    """Check if Playwright sync_api is importable."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:
        return False


def is_chromium_available() -> bool:
    """Check if Chromium browser is installed and executable for Playwright."""
    if not is_playwright_available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            return bool(exe and Path(exe).exists())
    except Exception:
        return False


def _is_private_ip(hostname: str) -> bool:
    """Check whether a hostname or IP string resolves to a private/loopback/link-local address."""
    clean_host = hostname.strip().lower()
    if clean_host in ("localhost", "0.0.0.0", "127.0.0.1", "::1"):
        return True

    # Check if host is direct IP address
    try:
        ip = ipaddress.ip_address(clean_host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
    except ValueError:
        pass

    # If it's a domain name, check resolved IPs if possible
    try:
        for addrinfo in socket.getaddrinfo(clean_host, None):
            sock_addr = addrinfo[4][0]
            ip = ipaddress.ip_address(sock_addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                return True
    except Exception:
        # If DNS resolution fails, allow downstream network handling or block if suspicious
        if clean_host.endswith(".internal") or clean_host.endswith(".local"):
            return True

    return False


def validate_capture_url(
    url: str,
    mode: str = "EVIDENCE",
    trusted_urls: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Strict security validation of capture target URLs.

    Modes:
    - EVIDENCE: Only public http/https URLs originating from verified sources. Reject private IPs/localhost.
    - LOCAL_WEB_APP: Only localhost and 127.0.0.1 on http/https. Reject public/external hosts.
    """
    if not url or not url.strip():
        return False, "EMPTY_URL: Target URL is empty."

    raw_url = url.strip()
    parsed = urlparse(raw_url)

    # 1. Reject invalid schemes
    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"DISALLOWED_SCHEME: Scheme '{parsed.scheme}' is forbidden. Only http/https permitted."

    hostname = parsed.hostname
    if not hostname:
        return False, f"INVALID_URL: URL '{raw_url}' has no valid hostname."

    hostname_lower = hostname.lower()

    if mode == "LOCAL_WEB_APP":
        # Local web app mode strictly allows localhost / 127.0.0.1 only
        if hostname_lower not in ("localhost", "127.0.0.1"):
            return False, f"DISALLOWED_LOCAL_HOST: Local app capture only allows localhost/127.0.0.1, got '{hostname}'."
        return True, "VALID_LOCAL_URL"

    # EVIDENCE mode: Must be public external URL
    if _is_private_ip(hostname_lower):
        return False, f"PRIVATE_IP_BLOCKED: Evidence capture rejects private IP or loopback address '{hostname}'."

    # If trusted_urls list is provided, target URL must originate from a verified source
    if trusted_urls is not None:
        normalized_target = raw_url.lower()
        matched = False
        for t_url in trusted_urls:
            t_clean = (t_url or "").strip().lower()
            if not t_clean:
                continue
            # Match exact, or target url starts with trusted prefix, or matching domains
            t_parsed = urlparse(t_clean)
            if t_parsed.hostname and t_parsed.hostname == hostname_lower:
                matched = True
                break
            if normalized_target.startswith(t_clean):
                matched = True
                break
        if not matched:
            return False, f"UNTRUSTED_SOURCE_URL: URL '{raw_url}' does not originate from verified ResearchDossier sources."

    return True, "VALID_EVIDENCE_URL"


class WebCaptureService:
    """Safe, isolated browser evidence capture and local UI demonstration capture."""

    def __init__(
        self,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        default_timeout_ms: int = 8000,
    ):
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.default_timeout_ms = default_timeout_ms

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            try:
                return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
            except Exception:
                return ImageFont.load_default()

    def composite_evidence_shot(
        self,
        raw_screenshot_path: Path,
        output_path: Path,
        source_domain: str,
        document_title: str,
        target_width: int = 1080,
        target_height: int = 1920,
    ) -> Tuple[str, str]:
        """Composite raw web capture onto 9:16 portrait video frame with subtle source badge."""
        canvas = Image.new("RGB", (target_width, target_height), color=(15, 23, 42))
        draw = ImageDraw.Draw(canvas)

        # 1. Header Badge: Source Domain & Document Title
        badge_y = 120
        badge_h = 76
        draw.rounded_rectangle(
            [60, badge_y, target_width - 60, badge_y + badge_h],
            radius=16,
            fill=(24, 34, 53),
            outline=(59, 130, 246),
            width=2,
        )

        domain_font = self._get_font(24)
        title_font = self._get_font(20)

        # Truncate clean domain and title
        dom_text = f"🌐 {source_domain.upper()}"
        draw.text((90, badge_y + 24), dom_text, font=domain_font, fill=(96, 165, 250), anchor="ls")

        clean_title = document_title[:45] if document_title else "Official Documentation"
        draw.text((90, badge_y + 54), clean_title, font=title_font, fill=(203, 213, 225), anchor="ls")

        # Verified badge pill on right
        ver_f = self._get_font(18)
        draw.rounded_rectangle(
            [target_width - 210, badge_y + 18, target_width - 90, badge_y + 58],
            radius=10,
            fill=(16, 185, 129),
        )
        draw.text((target_width - 150, badge_y + 38), "EVIDENCE", font=ver_f, fill=(15, 23, 42), anchor="mm")

        # 2. Embed Screenshot with safe framing
        if raw_screenshot_path.exists():
            try:
                sc = Image.open(raw_screenshot_path).convert("RGB")
                sc_w, sc_h = sc.size

                # Desired viewport box inside 9:16 frame
                max_w = target_width - 120
                max_h = target_height - badge_y - badge_h - 160

                scale = min(max_w / sc_w, max_h / sc_h, 1.0)
                new_w = max(10, int(sc_w * scale))
                new_h = max(10, int(sc_h * scale))

                sc_resized = sc.resize((new_w, new_h), Image.Resampling.LANCZOS)

                paste_x = (target_width - new_w) // 2
                paste_y = badge_y + badge_h + 40

                # Framing shadow / border
                draw.rounded_rectangle(
                    [paste_x - 4, paste_y - 4, paste_x + new_w + 4, paste_y + new_h + 4],
                    radius=12,
                    fill=(30, 41, 59),
                    outline=(71, 85, 105),
                    width=2,
                )
                canvas.paste(sc_resized, (paste_x, paste_y))
            except Exception as e:
                logger.warning(f"Failed to paste screenshot: {e}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, "PNG")

        content_bytes = output_path.read_bytes()
        sha = hashlib.sha256(content_bytes).hexdigest()
        return str(output_path), sha

    def capture_evidence(
        self,
        url: str,
        output_path: Path,
        source_excerpt: Optional[str] = None,
        source_title: Optional[str] = None,
        trusted_urls: Optional[List[str]] = None,
        timeout_ms: Optional[int] = None,
    ) -> Tuple[Optional[VisualAssetCandidate], List[str]]:
        """Capture webpage or documentation excerpt evidence using isolated Playwright Chromium."""
        failures = []

        # 1. Security validation
        is_valid, reason = validate_capture_url(url, mode="EVIDENCE", trusted_urls=trusted_urls)
        if not is_valid:
            return None, [f"SECURITY_VIOLATION: {reason}"]

        if not is_playwright_available() or not is_chromium_available():
            return None, ["PLAYWRIGHT_UNAVAILABLE: Chromium browser or Playwright not installed"]

        timeout = timeout_ms or self.default_timeout_ms
        from playwright.sync_api import sync_playwright

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_shot_path = output_path.with_name(f"raw_{output_path.name}")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": self.viewport_width, "height": self.viewport_height},
                    ignore_https_errors=False,
                    accept_downloads=False,
                    permissions=[],
                )
                page = context.new_page()
                page.set_default_timeout(timeout)

                response = page.goto(url, wait_until="domcontentloaded")
                if not response or response.status >= 400:
                    status_code = response.status if response else "NO_RESPONSE"
                    browser.close()
                    return None, [f"HTTP_ERROR: Target page returned status {status_code}"]

                parsed_url = urlparse(url)
                domain = parsed_url.hostname or "source"
                title = source_title or page.title() or domain

                # 2. Excerpt locator and highlight
                if source_excerpt and source_excerpt.strip():
                    clean_excerpt = source_excerpt.strip()
                    # Locate element containing excerpt
                    # First try text match via Playwright text locator
                    escaped_text = re.sub(r'["\\]', r"\\\g<0>", clean_excerpt[:80])
                    loc = page.locator(f"text={escaped_text}").first
                    found = False

                    try:
                        if loc.count() > 0:
                            loc.scroll_into_view_if_needed(timeout=2000)
                            # Apply temporary highlight style safely via evaluate on the specific found handle
                            loc.evaluate(
                                "el => { el.style.outline = '3px solid #3b82f6'; el.style.backgroundColor = 'rgba(59, 130, 246, 0.15)'; el.style.borderRadius = '6px'; el.style.padding = '4px'; }"
                            )
                            found = True
                    except Exception:
                        found = False

                    if not found:
                        # Try normalized keyword substring match across paragraphs / code / headings
                        words = clean_excerpt.lower().split()
                        if len(words) >= 3:
                            key_phrase = " ".join(words[:4])
                            esc_phrase = re.sub(r'["\\]', r"\\\g<0>", key_phrase)
                            loc_sub = page.locator(f"text={esc_phrase}").first
                            try:
                                if loc_sub.count() > 0:
                                    loc_sub.scroll_into_view_if_needed(timeout=2000)
                                    loc_sub.evaluate(
                                        "el => { el.style.outline = '3px solid #3b82f6'; el.style.backgroundColor = 'rgba(59, 130, 246, 0.15)'; el.style.borderRadius = '6px'; el.style.padding = '4px'; }"
                                    )
                                    found = True
                            except Exception:
                                found = False

                    if not found:
                        # Excerpt not found: DO NOT FABRICATE SCREENSHOT!
                        browser.close()
                        return None, [f"EVIDENCE_TEXT_NOT_FOUND: Excerpt '{clean_excerpt[:50]}' was not found on '{url}'"]

                # 3. Take screenshot
                page.screenshot(path=str(raw_shot_path), full_page=False)
                browser.close()

            # 4. Composite final 9:16 card
            comp_path, comp_sha = self.composite_evidence_shot(
                raw_screenshot_path=raw_shot_path,
                output_path=output_path,
                source_domain=domain,
                document_title=title,
            )

            # Cleanup raw screenshot if different
            if raw_shot_path.exists() and raw_shot_path != output_path:
                try:
                    raw_shot_path.unlink()
                except Exception:
                    pass

            candidate = VisualAssetCandidate(
                candidate_id=f"cand_{output_path.stem}",
                source_type=VisualSourceType.RESEARCH_SOURCE,
                file_path=comp_path,
                source_url=url,
                license_type="Document Citation",
                attribution=domain,
                content_sha256=comp_sha,
                width=1080,
                height=1920,
                acquisition_method="playwright_web_evidence",
                is_synthetic=False,
            )
            return candidate, []

        except Exception as e:
            failures.append(f"CAPTURE_FAILED: {type(e).__name__}: {str(e)}")
            return None, failures

    def capture_local_ui(
        self,
        url: str,
        output_path: Path,
        interaction_plan: Optional[List[BrowserAction]] = None,
        timeout_ms: Optional[int] = None,
    ) -> Tuple[Optional[VisualAssetCandidate], List[str]]:
        """Safely capture a demonstration interface from a local web application.

        Executes typed BrowserActions only (strictly prohibits arbitrary JavaScript injection).
        """
        failures = []

        is_valid, reason = validate_capture_url(url, mode="LOCAL_WEB_APP")
        if not is_valid:
            return None, [f"SECURITY_VIOLATION: {reason}"]

        if not is_playwright_available() or not is_chromium_available():
            return None, ["PLAYWRIGHT_UNAVAILABLE: Chromium browser or Playwright not installed"]

        timeout = timeout_ms or self.default_timeout_ms
        from playwright.sync_api import sync_playwright

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_shot_path = output_path.with_name(f"raw_{output_path.name}")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": self.viewport_width, "height": self.viewport_height},
                    ignore_https_errors=True,
                )
                page = context.new_page()
                page.set_default_timeout(timeout)

                # 1. Navigate to local application
                page.goto(url, wait_until="domcontentloaded")

                # 2. Execute typed browser actions
                if interaction_plan:
                    for idx, action in enumerate(interaction_plan):
                        a_type = action.action_type
                        act_timeout = action.timeout_ms or 2000

                        if a_type == BrowserActionType.NAVIGATE:
                            nav_url = action.value or action.text or url
                            # Validate nested navigate targets as well
                            val_ok, val_why = validate_capture_url(nav_url, mode="LOCAL_WEB_APP")
                            if not val_ok:
                                browser.close()
                                return None, [f"SECURITY_VIOLATION: Action {idx} NAVIGATE {val_why}"]
                            page.goto(nav_url, timeout=act_timeout)

                        elif a_type == BrowserActionType.CLICK:
                            if not action.selector:
                                continue
                            page.click(action.selector, timeout=act_timeout)

                        elif a_type == BrowserActionType.TYPE:
                            if not action.selector:
                                continue
                            text_val = action.text or action.value or ""
                            page.fill(action.selector, text_val, timeout=act_timeout)

                        elif a_type == BrowserActionType.WAIT_FOR:
                            if not action.selector:
                                continue
                            page.wait_for_selector(action.selector, timeout=act_timeout)

                        elif a_type == BrowserActionType.SCROLL:
                            scroll_dy = int(action.value or 300)
                            page.mouse.wheel(0, scroll_dy)

                        elif a_type == BrowserActionType.SCREENSHOT:
                            pass

                # 3. Capture screenshot
                page.screenshot(path=str(raw_shot_path), full_page=False)
                browser.close()

            # 4. Composite onto 9:16 frame
            comp_path, comp_sha = self.composite_evidence_shot(
                raw_screenshot_path=raw_shot_path,
                output_path=output_path,
                source_domain="localhost",
                document_title="Local UI Demo",
            )

            if raw_shot_path.exists() and raw_shot_path != output_path:
                try:
                    raw_shot_path.unlink()
                except Exception:
                    pass

            candidate = VisualAssetCandidate(
                candidate_id=f"cand_{output_path.stem}",
                source_type=VisualSourceType.LOCAL_WEB_APP,
                file_path=comp_path,
                source_url=url,
                license_type="Local Demonstration",
                attribution="Local UI",
                content_sha256=comp_sha,
                width=1080,
                height=1920,
                acquisition_method="playwright_local_ui",
                is_synthetic=False,
            )
            return candidate, []

        except Exception as e:
            failures.append(f"LOCAL_CAPTURE_FAILED: {type(e).__name__}: {str(e)}")
            return None, failures
