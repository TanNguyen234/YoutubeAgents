# Packaging Engine Architecture & Operation (Phase 1)

> **Core Objective**: Replace generic 3-template titles ("The Secret Truth About...", "Why Nobody Talks About...", "I Tried...") and simplistic 4-word keyword thumbnail generation with a grounded tournament of exactly 3 distinct title + thumbnail candidate packages. Evaluate them via structured semantic truth/grounding gates, project asset provenance, title-thumbnail complementarity, strategic diversity, and observable offline quality scoring. The winner is selected as default for publication (`SEOPackage.selected_title` and active `ThumbnailPackage`), while all 3 candidates are preserved in the `packaging_tournaments` table for future experimentation.

---

## 1. What Packaging Engine Measures vs What It Does NOT Measure

### What It Measures (Deterministic Offline Signals)
- **Factual Grounding & Truth Boundary**: Evaluates factual propositions asserted or presupposed by candidate titles against verified claims from `FactCheckReport`. The reasoning backend extracts propositions and maps them to verified claim IDs, which are validated server-side against `PackagingContext.verified_claim_ids`. Pure topic framing angles (educational guides, conceptual overviews) pass as `NON_FACTUAL_FRAMING`, while propositions lacking verified claim support or containing false presuppositions (even in "Why..." questions) are strictly rejected as `UNSUPPORTED`.
- **Hard Technical Constraints**: Strict character length enforcement ($\le 100$ characters per YouTube metadata spec) and visual layout boundaries (headline $\le 4$ words to prevent mobile UI truncation).
- **Asset Provenance & Image Integrity**: The subject asset ID and any supporting asset IDs proposed for a thumbnail must belong to the current project's registered assets, and files must exist on disk before rendering. Every rendered thumbnail receives a SHA-256 hash computed directly from the rendered image bytes.
- **Candidate Diversity & Fail-Closed Enforcement**: Evaluates token Jaccard overlap between candidate titles ($< 0.70$) and verifies visual strategy and headline diversity. If candidates fail diversity, exactly one bounded correction pass is executed, followed by a mandatory second diversity check. If diversity remains unresolved, the tournament fails closed with `PackagingError("PACKAGING_DIVERSITY_UNRESOLVED")` and aborts before any downstream package mutation.
- **Title $\leftrightarrow$ Thumbnail Complementarity**: Evaluates whether the title and thumbnail headline work together synergistically rather than blindly repeating identical text strings (headline repeating title text is penalized; complementary contrast is rewarded).
- **Multi-Factor Offline Quality Score**: Weighted scoring covering visual contrast, text readability, click motivation strength, title-thumbnail complementarity, and character length efficiency on a normalized scale of **0.0 – 1.0**.

### What It Does NOT Measure (Explicit Limitations & Boundaries)
> [!WARNING]
> **Offline packaging quality score is a heuristic rubric, NOT real YouTube CTR.**
> - The system does **NOT** invent or hallucinate fake performance metrics (e.g. "Predicted CTR: 8.6%").
> - True CTR is only observable after real YouTube users see impressions in production analytics.
> - **Native YouTube Studio A/B Testing Readiness**: In Phase 1, `native_ab_eligible` is a **local format/packaging readiness flag**, indicating that the video is long-form (16:9), channel is not `made_for_kids`, and all 3 candidates passed verification gates. It is **NOT** proof that YouTube Studio will allow the experiment at execution time (which depends on external account features, copyright standing, live premiere state, and Studio eligibility requirements). Phase 1 does **NOT automate** YouTube Studio via browser automation or unofficial endpoints.
> - **Platform Format Semantics**:
>   - **Long-Form (16:9)**: Primary format rendered at $1280 \times 720$. Prepares 3 valid tournament candidates for local A/B readiness (`native_ab_eligible = True` when all 3 pass gates and channel is not `made_for_kids`).
>   - **Shorts (9:16)**: Primary format rendered at $1080 \times 1920$. YouTube Studio desktop supports custom thumbnail uploads for Shorts, so custom 9:16 thumbnail artifacts are generated and retained. However, YouTube does not provide native A/B testing for Shorts, so `native_ab_eligible = False`.
> - **Visual Strategy Execution**: The renderer does NOT synthesize new semantic artwork (e.g. generating architecture diagrams or benchmark charts from nothing). Rather, it executes bounded pixel-level composition branches (`FOCUS`, `SPLIT_CONTRAST`, `DETAIL_CROP`) over existing, trusted project image assets.

---

## 2. Tournament Pipeline Architecture

```
Authoritative Project Artifacts
(VideoProject, Script, ResearchDossier, FactCheckReport, Assets)
                    │
                    ▼
     PackagingContext Construction
  (Canonical script hooks, verified claims,
   project asset IDs, target audience)
                    │
                    ▼
       Candidate Proposal Generation
    (ReasoningBackend: Exactly 3 Proposals:
     DIRECT_VALUE, CONTRAST_MECHANISM, CURIOSITY_QUESTION)
                    │
                    ▼
        Gate 4: Initial Diversity Check
        ├── If Pass ──┐
        └── If Fail ──▼
            Bounded 1-Pass Diversity Correction
            └── Mandatory Re-Check
                ├── Pass -> Status: CORRECTED
                └── Fail -> Fail Closed (PackagingError)
                    │
                    ▼
       Hard Verification & Safety Gates
     - Title Length Gate (<= 100 chars)
     - Headline Word Count Gate (<= 4 words)
     - Subject Asset Provenance Gate
     - Semantic Truth & Grounding Gate
       (Server-validated against verified claims)
                    │
                    ▼
        Candidate Thumbnail Rendering
     - Materially executes strategy composition branch:
       * FOCUS: standard framing + left pill banner
       * SPLIT_CONTRAST: side-by-side split (or panel contrast fallback)
       * DETAIL_CROP: 1.5x zoom center-crop + bottom pill banner
     - Generates 16:9 (1280x720) and 9:16 (1080x1920)
     - Computes real content SHA-256 from rendered image bytes
                    │
                    ▼
     Complementarity & Offline Scoring
     (Normalized 0.0 - 1.0 offline quality score)
                    │
                    ▼
        Deterministic Winner Selection
     (Highest quality score -> highest complementarity
      -> shortest title -> stable candidate ID)
                    │
                    ▼
   Persistence & Downstream Contract Updates
     - SEOPackage.selected_title = Winner Title
     - SEOPackage.title_variants = Gate-Passed Candidates Only
     - Active ThumbnailPackage = Real Rendered Image & SHA
     - packaging_tournaments Table (Schema v7)
```

---

## 3. Database Schema (Schema Version 7)

```sql
CREATE TABLE IF NOT EXISTS packaging_tournaments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    selected_candidate_id TEXT,
    selection_reason TEXT NOT NULL,
    native_ab_eligible INTEGER NOT NULL DEFAULT 0,
    scoring_version TEXT NOT NULL DEFAULT 'v1.0',
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_packaging_tournaments_project
ON packaging_tournaments(project_id);
```

Each candidate stored in `candidates_json` retains:
- `id`, `title`, `title_strategy`, `title_truth_status`, `grounding_claim_id`
- `thumbnail_headline`, `thumbnail_visual_strategy`, `subject_asset_id`, `supporting_asset_ids`
- `requested_visual_strategy`, `actual_visual_strategy`, `visual_fallback_reason`
- `file_path_16_9`, `file_path_9_16`, `content_sha256`
- `click_motivation_rationale`, `complementarity_score`, `quality_score`, `score_breakdown`

---

## 4. Downstream Publisher Invariance

Downstream publishing logic (`YouTubePublisherService` and `PipelineBrain`) operates seamlessly:
1. `SEOPackage.selected_title` is populated with the tournament winner's title.
2. `SEOPackage.title_variants` is populated with only **gate-passed** candidate titles (rejected titles are excluded from publishable variants, while remaining in tournament audit history).
3. The active `ThumbnailPackage` is created using the winner's real 16:9 rendered file path, headline text, and actual file SHA-256.
4. `YouTubePublisherService.build_metadata_payload()` consumes `SEOPackage.selected_title` and `ThumbnailPackage.file_path_16_9` as before.
