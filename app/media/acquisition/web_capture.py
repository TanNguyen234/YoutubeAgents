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
    clean_ip_str = clean_host.strip("[]")
    if clean_ip_str in ("localhost", "0.0.0.0", "127.0.0.1", "::1", "169.254.169.254") or clean_host in ("localhost", "0.0.0.0", "127.0.0.1", "::1"):
        return True
    if clean_host in ("metadata.google.internal", "metadata.local", "instance-data", "169.254.169.254"):
        return True

    # Check if host is direct IP address (IPv4 or IPv6)
    try:
        ip = ipaddress.ip_address(clean_ip_str)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved:
            return True
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            if ip.ipv4_mapped.is_private or ip.ipv4_mapped.is_loopback or ip.ipv4_mapped.is_link_local:
                return True
    except ValueError:
        pass

    # If it's a domain name, check resolved IPs if possible
    try:
        addrinfos = socket.getaddrinfo(clean_host, None)
        for addrinfo in addrinfos:
            sock_addr = addrinfo[4][0]
            ip = ipaddress.ip_address(sock_addr.strip("[]"))
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved:
                return True
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                if ip.ipv4_mapped.is_private or ip.ipv4_mapped.is_loopback or ip.ipv4_mapped.is_link_local:
                    return True
    except Exception:
        # If DNS resolution fails, allow downstream network handling or block if suspicious
        if clean_host.endswith(".internal") or clean_host.endswith(".local") or clean_host.endswith(".onion"):
            return True

    return False


def is_within_canonical_url_scope(target_url: str, canonical_url: str) -> bool:
    """Evaluate whether target_url is within the permitted CANONICAL_URL_SCOPE of canonical_url.

    Permits the canonical URL itself and explicitly descendant subpaths.
    Requires identical scheme, identical hostname (preventing hostname-prefix attacks),
    and identical port.
    """
    if not target_url or not canonical_url:
        return False
    try:
        t_parsed = urlparse(target_url.strip())
        c_parsed = urlparse(canonical_url.strip())
    except Exception:
        return False

    # Scheme match
    if (t_parsed.scheme or "").lower() != (c_parsed.scheme or "").lower():
        return False

    # Hostname match (parsed host, prevents "docs.example.com.evil.com")
    t_host = (t_parsed.hostname or "").lower()
    c_host = (c_parsed.hostname or "").lower()
    if not t_host or not c_host or t_host != c_host:
        return False

    # Port match
    if t_parsed.port != c_parsed.port:
        return False

    # Path scope
    t_path = (t_parsed.path or "/").rstrip("/")
    c_path = (c_parsed.path or "/").rstrip("/")

    # If canonical path is root or empty, any path on same host/port is descendant
    if not c_path:
        return True

    # Exact path match or descendant path
    if t_path.lower() == c_path.lower():
        return True
    if t_path.lower().startswith(c_path.lower() + "/"):
        return True

    return False


def validate_capture_url(
    url: str,
    mode: str = "EVIDENCE",
    trusted_urls: Optional[List[str]] = None,
    check_dns: bool = True,
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
        # Local web app mode strictly allows localhost / 127.0.0.1 / ::1 only
        clean_ip = hostname_lower.strip("[]")
        if clean_ip not in ("localhost", "127.0.0.1", "::1"):
            return False, f"DISALLOWED_LOCAL_HOST: Local app capture only allows localhost/127.0.0.1, got '{hostname}'."
        return True, "VALID_LOCAL_URL"

    # EVIDENCE mode:
    # 2. If trusted_urls list is provided, target URL must originate from a verified source
    if trusted_urls is not None:
        matched = any(is_within_canonical_url_scope(raw_url, t_url) for t_url in trusted_urls if t_url)
        if not matched:
            return False, f"UNTRUSTED_SOURCE_URL: URL '{raw_url}' does not originate from verified ResearchDossier sources."

    # 3. Reject private IP / loopback / link-local / metadata addresses
    if _is_private_ip(hostname_lower):
        return False, f"PRIVATE_IP_BLOCKED: Evidence capture rejects private IP or loopback address '{hostname}'."

    # 4. Check DNS resolution fail-closed for non-numeric domain names
    if check_dns:
        clean_ip = hostname_lower.strip("[]")
        is_numeric_ip = False
        try:
            ipaddress.ip_address(clean_ip)
            is_numeric_ip = True
        except ValueError:
            pass

        if not is_numeric_ip:
            try:
                addrinfos = socket.getaddrinfo(clean_ip, None)
                if not addrinfos:
                    return False, f"DNS_RESOLUTION_FAILED: Hostname '{hostname}' could not be resolved."
            except socket.gaierror:
                return False, f"DNS_RESOLUTION_FAILED: Hostname '{hostname}' could not be resolved via DNS."

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

    def _create_route_handler(self):
        """Create a route handler enforcing SSRF defense, private IP blocks, and protocol restrictions."""
        def handle_route(route):
            req = route.request
            req_url = req.url
            parsed_req = urlparse(req_url)
            scheme = parsed_req.scheme.lower()
            if scheme in ("data", "blob"):
                route.continue_()
                return
            if scheme not in ("http", "https"):
                route.abort("blockedbyclient")
                return

            sub_host = (parsed_req.hostname or "").lower()
            # Never permit requests to private/internal/metadata IPs or hostnames,
            # even for initial navigation (DNS rebinding / TOCTOU defense).
            if _is_private_ip(sub_host):
                route.abort("blockedbyclient")
                return

            route.continue_()

        return handle_route

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
                    service_workers="block",
                )

                # Attach network routing at context boundary to intercept all pages, frames, and popups
                handle_route = self._create_route_handler()
                context.route("**/*", handle_route)

                page = context.new_page()
                page.set_default_timeout(timeout)

                # Automatically terminate unexpected popup windows or secondary pages
                context.on("page", lambda new_page: new_page.close() if new_page != page else None)

                response = page.goto(url, wait_until="domcontentloaded")
                if not response or response.status >= 400:
                    status_code = response.status if response else "NO_RESPONSE"
                    browser.close()
                    return None, [f"HTTP_ERROR: Target page returned status {status_code}"]

                # Post-navigation redirect revalidation
                final_url = page.url
                final_valid, final_reason = validate_capture_url(
                    final_url, mode="EVIDENCE", trusted_urls=trusted_urls, check_dns=True
                )
                if not final_valid:
                    browser.close()
                    return None, [f"SECURITY_VIOLATION: Navigation redirected to disallowed target '{final_url}': {final_reason}"]

                parsed_url = urlparse(final_url or url)
                domain = parsed_url.hostname or "source"
                title = source_title or page.title() or domain

                # 2. Excerpt locator and highlight
                if source_excerpt and source_excerpt.strip():
                    clean_excerpt = source_excerpt.strip()
                    locator_script = """
                    (targetExcerpt) => {
                        function normalize(text) {
                            return (text || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                        }
                        function stripPunct(text) {
                            return text.replace(/[^\\w\\s]/g, '');
                        }

                        const rawNorm = normalize(targetExcerpt);
                        const punctNorm = stripPunct(rawNorm);
                        const words = punctNorm.split(/\\s+/).filter(w => w.length > 0);

                        const elements = Array.from(document.querySelectorAll(
                            'p, div, span, h1, h2, h3, h4, h5, h6, li, td, pre, code, blockquote, article, section, em, strong'
                        ));

                        let bestEl = null;
                        let minLen = Infinity;

                        // 1. Exact or punctuation-stripped normalized substring containment
                        for (const el of elements) {
                            const elText = normalize(el.innerText || el.textContent);
                            if (elText.includes(rawNorm) || (punctNorm.length > 10 && stripPunct(elText).includes(punctNorm))) {
                                if (elText.length < minLen) {
                                    minLen = elText.length;
                                    bestEl = el;
                                }
                            }
                        }

                        // 2. Substantial normalized continuous subphrase (>= 60% of words, min 6 words)
                        if (!bestEl && words.length >= 6) {
                            const reqCount = Math.max(6, Math.ceil(words.length * 0.60));
                            const candidateSlices = [];
                            for (let i = 0; i <= words.length - reqCount; i++) {
                                candidateSlices.push(words.slice(i, i + reqCount).join(' '));
                            }

                            for (const el of elements) {
                                const elTextClean = stripPunct(normalize(el.innerText || el.textContent));
                                for (const slice of candidateSlices) {
                                    if (elTextClean.includes(slice)) {
                                        if (elTextClean.length < minLen) {
                                            minLen = elTextClean.length;
                                            bestEl = el;
                                        }
                                        break;
                                    }
                                }
                            }
                        }

                        if (bestEl) {
                            bestEl.scrollIntoView({ behavior: 'instant', block: 'center' });
                            bestEl.style.outline = '3px solid #3b82f6';
                            bestEl.style.backgroundColor = 'rgba(59, 130, 246, 0.15)';
                            bestEl.style.borderRadius = '6px';
                            bestEl.style.padding = '4px';
                            return true;
                        }
                        return false;
                    }
                    """
                    try:
                        found = page.evaluate(locator_script, clean_excerpt)
                    except Exception:
                        found = False

                    if not found:
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
                source_url=final_url or url,
                license_type=None,
                attribution=domain,
                content_sha256=comp_sha,
                width=1080,
                height=1920,
                acquisition_method="playwright_web_evidence",
                is_synthetic=False,
                raw_metadata={"requested_source_url": url},
            )
            return candidate, []

        except Exception as e:
            err_msg = str(e)
            if "ERR_BLOCKED_BY_CLIENT" in err_msg or "blockedbyclient" in err_msg:
                failures.append(f"SECURITY_VIOLATION: Navigation or subresource blocked by security policy: {err_msg}")
            else:
                failures.append(f"CAPTURE_FAILED: {type(e).__name__}: {err_msg}")
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
                    service_workers="block",
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
