# Visual Acquisition & Media Provenance Architecture

This document specifies the provenance resolution, security boundaries, and cache invalidation policies governing Visual Acquisition Phase 1 in the autonomous YouTube video pipeline.

---

## 1. Provenance Resolution & Grounding Contract

### Canonical Source Resolution Hierarchy
When an evidence shot requires document evidence (`VisualModality.DOCUMENT_EVIDENCE` or `VisualModality.SCREENSHOT`), visual acquisition strictly resolves the canonical target URL according to deterministic server-side authority:
1. **Binding Source Reference**: `EvidenceBinding.source_ref` maps to a verified `ResearchSource` in `ResearchDossier.sources` (matching `id` or `source_id`).
2. **Shot Source Reference**: `ShotSpec.source_refs` / `request.source_refs` maps to a verified `ResearchSource` in `ResearchDossier.sources`.
3. **Canonical URL Matching**: `EvidenceBinding.source_url` matches a canonical source URL already present in the verified research dossier.
4. **No Self-Authorization**: `EvidenceBinding` alone is not authority to introduce or capture new remote URLs. If `ResearchDossier` is missing or the source cannot be resolved from the dossier, remote capture fails closed immediately with `UNTRUSTED_VISUAL_SOURCE`.
5. **Canonical URL Scope (`CANONICAL_URL_SCOPE`)**: Remote evidence acquisition is restricted to the canonical ResearchSource URL scope. The canonical URL itself and explicitly permitted descendant subpaths may be captured. Unrelated paths on the same host and hostname-prefix attacks (e.g. `docs.example.com.evil.com`) are rejected with `UNTRUSTED_VISUAL_SOURCE`.
6. **Screen Instruction Isolation**: LLM-generated `screen_instruction` text cannot inject untrusted remote URLs as evidence targets. Remote `SCREEN_CAPTURE` requires a verified `ResearchDossier` source.

### Render Manifest Provenance Tracking
`RenderManifest.visual_assets` captures complete auditable provenance for every selected shot using these exact 11 fields:
- `shot_id`: Corresponding narrative shot identifier.
- `path`: Local filesystem path to the rendered visual asset.
- `sha256`: SHA-256 digest of the generated asset file.
- `source_type`: Typed origin (`RESEARCH_SOURCE`, `FALLBACK_CARD`, `STOCK_MEDIA`, `RENDERED`, `LOCAL_WEB_APP`).
- `source_url`: Real resolved URL of the captured or cited source.
- `source_ref`: Dossier source reference linking back to verified research evidence.
- `license_type`: Real verified license (e.g. `PEXELS`, or `None` for fair-use citation cards; never fabricated).
- `attribution`: Real author/publisher attribution string.
- `evidence_claim_ids`: List of verified claim IDs grounded by this asset.
- `acquisition_method`: Mechanism used (`playwright_web_evidence`, `evidence_summary_card`, `diagram_renderer`, `playwright_local_ui`, etc.).
- `synthetic`: Boolean indicating whether the asset is AI-generated synthetic media.

---

## 2. Fallback Modality & Honest Representation

To prevent synthetic or misleading provenance claims:
- **No Masquerading**: Programmatically rendered citation cards (`EvidenceRenderer`) must NEVER emit `DOCUMENT_EVIDENCE` as `actual_modality` or `DOCUMENT` as `source_type`.
- **Honest Modality**: A fallback card returns `actual_modality = VisualModality.STATIC_CARD`, `source_type = "FALLBACK_CARD"`, `license_type = None`, and `acquisition_method = "evidence_summary_card"`.
- **No Fabricated Licenses**: Programmatic summary cards do not invent fake licenses like `"Document Citation"`. They emit `license_type = None`.
- **Stock Media Tracking**: Stock media candidates record real provider licenses (e.g. `PEXELS`) without inventing commercial terms.

---

## 3. Browser Network Boundary & Isolation Security

Web capture uses headless Playwright with strict network boundary constraints:
- **Service Worker Blocking**: Playwright browser contexts are created with `service_workers="block"`, preventing service workers from bypassing or interfering with context-level routing security checks.
- **Context-Level Routing**: Network routing is attached at the browser context boundary (`context.route("**/*", ...)`), ensuring all pages, iframes, and popups are intercepted.
- **Strict SSRF / Private IP Defense**: All requests targeting private, link-local, loopback, or cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`, `127.0.0.1`, `[::1]`, `10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`) are aborted with `blockedbyclient`.
- **No Initial Navigation Exemption**: Route validation evaluates the destination host at request time, mitigating DNS rebinding and TOCTOU attacks.
- **Popup Neutralization**: Any unexpected popups or secondary pages opened by target sites are terminated immediately via `context.on("page", ...)`.
- **Download Cancellation**: Unsolicited file downloads are cancelled automatically (`accept_downloads=False`).

---

## 4. Cache Invalidation & Production Fingerprints

### Fingerprint Derivation
Production requests are fingerprinted deterministically in `MediaProductionPipeline`:
$$\text{Production Fingerprint} = f(\text{narration\_sha256}, \text{voice}, \text{visual\_plan\_hash}, \dots)$$
The `visual_plan_hash` incorporates:
- Content format and creative profile.
- All scene narration and hook texts.
- **Persisted Research Dossier Hashes**: SHA-256 hashes of all `ResearchSource` records in the persisted `ResearchDossier`.

### Invalidation Triggers & Determinism Model
`MediaProductionPipeline.run_production()` invalidates cache reuse and forces regeneration when:
1. **Dossier Source Update**: Any source in the persisted dossier is modified (e.g., `content_sha256` or URL changes).
   *Note*: Cache invalidation reacts strictly to persisted `ResearchDossier` source snapshots. If a live upstream webpage changes but the `ResearchDossier` has not been refreshed, the pipeline relies on the recorded snapshot to preserve deterministic reproducibility.
2. **On-Disk Asset Tampering**: A cached visual asset file on disk is modified, corrupted, or deleted. The pipeline re-verifies disk SHA-256 against the cached manifest; any mismatch invalidates the cache and forces a full rebuild.
3. **Script or Profile Mutation**: Any narration, resolution, or creative profile change updates the request fingerprint.

### Synthetic Media Disclosure
- Synthetic disclosure (`contains_synthetic_media` / `is_synthetic`) evaluates ONLY final selected assets appearing on `ShotTimeline.shots`.
- Discarded proposals or failed generation attempts do not cause false-positive synthetic disclosure.
