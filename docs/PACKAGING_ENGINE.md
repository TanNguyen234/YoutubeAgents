# Packaging Engine Architecture & Operation (Phase 1)

> **Core Objective**: Replace generic 3-template titles ("The Secret Truth About...", "Why Nobody Talks About...", "I Tried...") and simplistic 4-word keyword thumbnail generation with a grounded tournament of exactly 3 distinct title + thumbnail candidate packages. Evaluate them via truth/grounding gates, asset provenance, complementarity, diversity, and observable offline quality scoring. The winner is selected as default for publication (`SEOPackage.selected_title` and active `ThumbnailPackage`), while all 3 candidates are preserved in `packaging_tournaments` table for future experimentation.

---

## 1. What Packaging Engine Measures vs What It Does NOT Measure

### What It Measures (Deterministic Offline Signals)
- **Factual Grounding & Truth Boundary**: Verifies that any factual claim asserted in a title directly matches a verified claim from `FactCheckReport` or general topic context. Non-factual framing angles (curiosity questions, conceptual hooks) are permitted with a safe classification, while ungrounded factual assertions are strictly rejected as `UNSUPPORTED`.
- **Hard Technical Constraints**: Strict character length enforcement ($\le 100$ characters per YouTube metadata spec) and visual layout boundaries (headline $\le 4$ words / $\le 28$ characters to prevent mobile UI overlap).
- **Asset Provenance & License Verification**: The subject asset ID proposed for a thumbnail must exist in the project's registered assets with verified license rights and content SHA-256 hash. Hallucinated or external unverified asset IDs are rejected.
- **Candidate Diversity**: Deterministic Jaccard token overlap between candidate titles ($< 0.80$) and thumbnail headlines ($< 0.80$), along with diverse visual strategies (`architecture_diagram`, `mechanism_breakdown`, `benchmark_comparison`, etc.).
- **Title $\leftrightarrow$ Thumbnail Complementarity**: Evaluates whether the title and thumbnail text work together synergistically rather than blindly repeating identical text strings (headline repeating title text is penalized; complementary contrast is rewarded).
- **Multi-Factor Offline Quality Score**: Weighted scoring covering visual saliency, text readability, click motivation strength, title-thumbnail complementarity, and character length efficiency (scale 0.0 – 10.0).

### What It Does NOT Measure (Explicit Limitations & Boundaries)
> [!WARNING]
> **Offline packaging quality score is a heuristic rubric, NOT real YouTube CTR.**
> - The system does **NOT** invent or hallucinate fake performance metrics (e.g. "Predicted CTR: 8.6%").
> - True CTR is only observable after real YouTube users see impressions in production analytics.
> - **Native YouTube Studio A/B Testing**: In Phase 1, YouTube Studio A/B testing is prepared in Schema and domain models (`native_ab_eligible = True` for eligible long-form videos), but is **NOT automated** via browser automation, Playwright, or unsupported private APIs. All 3 candidates are preserved in SQLite so a creator or future integration can configure the test in YouTube Studio manually or via official endpoints when supported.
> - **Platform Format Boundary**:
>   - **Long-Form (16:9)**: Eligible for the 3-candidate tournament, candidate thumbnail rendering (16:9 & 9:16), and YouTube Studio A/B testing (`native_ab_eligible = True`).
>   - **Shorts (9:16)**: Evaluated through the tournament to select the best grounded title and 9:16 thumbnail card, but marked `native_ab_eligible = False` because YouTube does not support custom thumbnail uploads or A/B testing for YouTube Shorts.

---

## 2. Tournament Pipeline Architecture

```
Authoritative Project Artifacts
(VideoProject, Script, ResearchDossier, FactCheckReport, Assets)
                    │
                    ▼
     PackagingContext Construction
  (Canonical script hooks, verified claims,
   provenanced asset IDs, target audience)
                    │
                    ▼
       Candidate Proposal Generation
    (ReasoningBackend: Exactly 3 Proposals:
     DIRECT_VALUE, CONTRAST_MECHANISM, CURIOSITY_QUESTION)
                    │
                    ▼
       Hard Verification & Safety Gates
     - Factual Truth Gate (Claim Grounding)
     - Title Length Gate (<= 100 chars)
     - Subject Asset Provenance Gate
     - Diversity Gate (< 0.80 overlap)
       (Bounded 1-pass correction if needed)
                    │
                    ▼
        Candidate Thumbnail Rendering
     (ThumbnailDesigner: 16:9 & 9:16 variants,
      SHA-256 computation, physical file check)
                    │
                    ▼
     Complementarity & Offline Scoring
     (Visual saliency, text readability,
      click motivation, length efficiency)
                    │
                    ▼
        Deterministic Tie-Breaking
     (Highest quality score -> highest complementarity
      -> shortest title -> stable candidate ID)
                    │
                    ▼
   Persistence & Downstream Contract Updates
     - SEOPackage.selected_title = Winner Title
     - Active ThumbnailPackage = Winner 16:9 Thumbnail
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
- `thumbnail_headline`, `thumbnail_visual_strategy`, `subject_asset_id`
- `file_path_16_9`, `file_path_9_16`, `content_sha256`
- `click_motivation_rationale`, `complementarity_score`, `quality_score`, `score_breakdown`

---

## 4. Downstream Publisher Invariance

Downstream publishing logic (`YouTubePublisherService` and `PipelineBrain`) requires zero breaking changes:
1. `SEOPackage.selected_title` is populated with the tournament winner's title.
2. `SEOPackage.title_variants` is populated with all 3 tournament candidate titles.
3. The active `ThumbnailPackage` is created from the winner's 16:9 rendered file path and headline text.
4. `YouTubePublisherService.build_metadata_payload()` reads `SEOPackage.selected_title` and `ThumbnailPackage.file_path_16_9` as before.
