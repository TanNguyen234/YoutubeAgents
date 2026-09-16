# Visual Acquisition & Media Provenance Architecture

This document specifies the provenance resolution, security boundaries, and cache invalidation policies governing Visual Acquisition Phase 1 in the autonomous YouTube video pipeline.

---

## 1. Provenance Resolution & Grounding Contract

### Canonical Source Resolution Hierarchy
When an evidence shot requires document evidence (`VisualModality.DOCUMENT_EVIDENCE` or `VisualModality.SCREENSHOT`), visual acquisition strictly resolves the canonical target URL according to deterministic server-side authority:
1. **Binding Source Reference**: `EvidenceBinding.source_ref` maps to a verified `ResearchSource` in `ResearchDossier.sources`. The source's canonical URL is used.
2. **Canonical URL Matching**: `EvidenceBinding.source_url` must match a canonical source URL present in the verified research dossier.
3. **Strict Path Authority**: Having `https://example.com/canonical/page.html` in the research dossier does NOT authorize arbitrary paths such as `https://example.com/other-user/unrelated.html` or different GitHub repositories on the same host. URL matching requires exact equality or verified sub-paths.
4. **Target URL Override Prohibition**: If an incoming request contains a `target_url` differing from the canonical source resolved via `source_ref`/`dossier`, it is rejected with `UNTRUSTED_VISUAL_SOURCE`.
5. **Screen Instruction Isolation**: LLM-generated `screen_instruction` text cannot inject untrusted remote URLs as evidence targets.

### Render Manifest Provenance Tracking
`RenderManifest.visual_assets` captures complete auditable provenance for every selected shot:
- `shot_id`: Corresponding narrative shot identifier.
- `path` & `sha256`: Local filesystem path and SHA-256 hash of the generated asset.
- `modality`: Requested modality.
- `actual_modality`: Modality actually rendered (e.g. `STATIC_CARD` if web capture was unavailable).
- `source_type`: Typed origin (`RESEARCH_SOURCE`, `FALLBACK_CARD`, `STOCK_MEDIA`, `RENDERED`).
- `source_url`: Real resolved URL of the captured or cited source.
- `source_ref`: Dossier source reference linking back to research evidence.
- `license_type`: Real verified license (e.g. `PEXELS`, or `None` for fair-use citation cards; never fabricated).
- `attribution`: Real author/publisher attribution.
- `evidence_claim_ids`: List of verified claim IDs grounded by this asset.
- `acquisition_method`: Mechanism used (`playwright_web_evidence`, `evidence_summary_card`, `diagram_renderer`, etc.).
- `synthetic`: Boolean indicating whether the asset is AI-generated synthetic media.

---

## 2. Fallback Modality & Honest Representation

To prevent synthetic or misleading provenance claims:
- **No Masquerading**: Programmatically rendered citation cards (`EvidenceRenderer`) must NEVER emit `DOCUMENT_EVIDENCE` as `actual_modality` or `DOCUMENT` as `source_type`.
- **Honest Modality**: A fallback card returns `actual_modality = VisualModality.STATIC_CARD`, `source_type = "FALLBACK_CARD"`, and `acquisition_method = "evidence_summary_card"`.
- **No Fabricated Licenses**: Programmatic summary cards do not invent fake licenses like `"Document Citation"`. They emit `license_type = None`.
- **Stock Media Tracking**: Stock media candidates record real provider licenses (e.g. `PEXELS`) and provider metadata without inventing commercial terms.

---

## 3. Browser Network Boundary & Isolation Security

Web capture uses headless Playwright with strict network boundary constraints:
- **Context-Level Routing**: Network routing is attached at the browser context boundary (`context.route("**/*", ...)`), ensuring all pages, iframes, and popups are intercepted.
- **Strict SSRF / Private IP Defense**: All requests targeting private, link-local, loopback, or cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`, `127.0.0.1`, `[::1]`, `10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`) are aborted with `blockedbyclient`.
- **No Initial Navigation Exemption**: Route validation evaluates the destination host at request time, mitigating DNS rebinding and TOCTOU attacks.
- **Popup Neutralization**: Any unexpected popups or secondary pages opened by target sites are terminated immediately via `context.on("page", ...)`.
- **Download Cancellation**: Unsolicited file downloads are cancelled automatically.

---

## 4. Cache Invalidation & Production Fingerprints

### Fingerprint Derivation
Production requests are fingerprinted deterministically in `MediaProductionPipeline`:
$$\text{Production Fingerprint} = f(\text{narration\_sha256}, \text{voice}, \text{visual\_plan\_hash}, \dots)$$
The `visual_plan_hash` incorporates:
- Content format and creative profile.
- All scene narration and hook texts.
- **Persisted Research Dossier Hashes**: SHA-256 hashes of all `ResearchSource` records in the persisted `ResearchDossier`.

### Invalidation Triggers
`MediaProductionPipeline.run_production()` invalidates cache reuse and forces regeneration when:
1. **Dossier Source Update**: Any source in the persisted dossier is modified (e.g., `content_sha256` or URL changes).
2. **On-Disk Asset Tampering**: A cached visual asset file on disk is modified, corrupted, or deleted. The pipeline re-verifies disk SHA-256 against the cached manifest; any mismatch forces full rebuild.
3. **Script or Profile Mutation**: Any narration or creative profile change updates the request fingerprint.

### Synthetic Media Disclosure
- Synthetic disclosure (`contains_synthetic_media`) evaluates ONLY final selected assets appearing on `ShotTimeline.shots`.
- Discarded proposals or failed generation attempts do not cause false-positive synthetic disclosure.
