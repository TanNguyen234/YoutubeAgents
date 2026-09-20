# Opportunity Engine Architecture & Operation (Phase 1)

> **Core Objective**: Select evidence-backed video topic opportunities from real, observable market signals on YouTube rather than having an LLM hallucinate demand and competition scores.

---

## 1. What Opportunity Engine Measures vs What It Does NOT Measure

### What It Measures
- **Market View Velocity**: Median and 75th percentile views per day across recently published comparable videos.
- **Recent Publishing Activity**: Fraction of sampled videos published within the last 30 and 7 days.
- **Supply & Saturation Proxy**: Approximate result count (`pageInfo.totalResults`) from YouTube Data API v3 search.
- **Creator Dominance / Concentration**: Unique creator count and the market share of the top creator in the sample.
- **Editorial Alignment & Niche Persona Fit**: Semantic fit of the candidate angle with channel persona evaluated via reasoning models.
- **Distinctness / Originality**: Jaccard token overlap and character sequence distance against recent channel topics and competing market titles.
- **Historical Channel Fit**: Measured correlation with real, top-performing published videos on the channel (strictly excluded if real analytics are absent).

### What It Does NOT Measure (Explicit Limitations)
> [!WARNING]
> **YouTube Data API signals are opportunity proxies, NOT exact monthly search volumes.**
> The YouTube Data API does not expose private search volume queries or platform-wide monthly impressions. Therefore:
> - No metrics in this system represent exact "monthly search volume" or "total audience demand".
> - `estimated_result_count` reflects approximate search index matches, which represents saturation/supply, never viewer demand.
> - CPM and revenue upside are explicitly set to `None` unless backed by a future dedicated Revenue Layer.

---

## 2. Market Signal Source & Collection Pipeline

- **Primary Source**: YouTube Data API v3 (`search.list` and `videos.list`).
- **Authentication**: Reuses existing `YouTubeOAuthManager` access token (`Authorization: Bearer <token>`) or `YOUTUBE_API_KEY` environment variable.
- **Bounded Ingestion Flow**:
  ```
  Channel Context (Niche, Tags)
         │
         ├─► Resolve Seed Queries (Max 2 seed queries)
         │
         ├─► YouTube search.list (Max 10 results per query)
         │     └─► 1 call/unit in dedicated Search Queries bucket (limit: 100/day)
         │
         ├─► YouTube videos.list (Statistics, ContentDetails, Snippet)
         │     └─► 1 unit in General Data API bucket (limit: 10,000/day)
         │
         ├─► Normalize MarketVideoObservation:
         │     [video_id, title, channel_id, published_at, view_count, duration]
         │
         ├─► Calculate Derived Metrics & Persist MarketSignalSnapshot (Schema v6)
         │
         └─► Build & Persist OpportunityPortfolio (opportunity_portfolios table, Schema v6)
  ```

---

## 3. Deterministic Proxy Formulas (Formula Version: `v1.0`)

For each video $i$ in a sample of size $N \ge 1$:
$$\text{age\_days}_i = \max\left(\frac{\text{collected\_at} - \text{published\_at}_i}{86400}, 0.5\right)$$
$$\text{views\_per\_day}_i = \frac{\text{view\_count}_i}{\text{age\_days}_i}$$

### 1. Demand Proxy (0.0 – 10.0 scale)
Combines median and 75th-percentile velocity to dampen extreme viral outliers while rewarding strong baseline velocity:
$$\text{raw\_demand} = 0.70 \times \text{median}(\text{views\_per\_day}) + 0.30 \times P_{75}(\text{views\_per\_day})$$

> [!IMPORTANT]
> **Viable Cohort Normalization (Anti-Distortion Invariant)**:
> Batch-relative normalization bounds ($\min(\text{raw\_demand})$ and $\max(\text{raw\_demand})$) are computed strictly from **viable candidates** ($N \ge 5$ and `confidence == "HIGH"`).
> Candidates with `INSUFFICIENT_SIGNAL` are completely excluded from these min/max bounds so that extreme outliers cannot distort viable candidate scores, ranks, or winners.
> Viable candidates are scaled across the viable cohort:
$$\text{Demand Score} = 3.0 + 7.0 \times \frac{\text{raw\_demand} - \min_{\text{viable}}(\text{raw\_demand})}{\max_{\text{viable}}(\text{raw\_demand}) - \min_{\text{viable}}(\text{raw\_demand})}$$
*(For single viable candidates, equal velocities, or insufficient candidates, logarithmic scaling applies: $\min(10.0, \max(1.0, \log_{10}(\text{raw\_demand}) \times 2.0))$.)*

### 2. Freshness / Trend Momentum Proxy (0.0 – 10.0 scale)
Reflects current publishing activity and velocity momentum:
$$\text{recent\_share\_30d} = \frac{\text{count}(\text{age\_days} \le 30)}{N}$$
$$\text{momentum} = \frac{\text{count}(\text{age\_days} \le 7)}{\max(1, \text{count}(\text{age\_days} \le 30))}$$
$$\text{Freshness Score} = \min\left(10.0, \max\left(0.0, (0.60 \times \text{recent\_share\_30d} + 0.40 \times \text{momentum}) \times 10.0\right)\right)$$

### 3. Competition Opportunity Proxy (0.0 – 10.0 scale)
Higher score indicates lower saturation and lower creator monopoly (blue ocean):
$$\text{creator\_diversity} = 1.0 - \text{top\_creator\_share}$$
$$\text{saturation} = \min\left(1.0, \frac{\text{estimated\_result\_count}}{100,000}\right)$$
$$\text{Competition Score} = \min\left(10.0, \max\left(0.0, (0.70 \times \text{creator\_diversity} + 0.30 \times (1.0 - \text{saturation})) \times 10.0\right)\right)$$

### 4. Originality Score (0.0 – 10.0 scale)
Evaluates token and sequence differentiation against both recent channel uploads and competing titles:
$$\text{Originality Score} = \min(10.0, \max(0.0, (1.0 - \max(\text{channel\_similarity}, \text{market\_similarity})) \times 10.0))$$

### 5. Composite Opportunity Score & Weights
```yaml
opportunity_weights:
  demand: 0.30
  freshness: 0.20
  competition: 0.15
  channel_fit: 0.15
  originality: 0.10
  historical_fit: 0.10
```
When `historical_fit` is `None`, weights dynamically renormalize across the remaining 5 active dimensions:
$$\text{Composite Score} = \frac{\sum_{d \in \text{active}} w_d \times S_d}{\sum_{d \in \text{active}} w_d}$$

---

## 4. LLM Trust Boundaries

| Dimension / Field | Computed By | May Reasoning Model Produce? |
| :--- | :--- | :--- |
| **demand** | Deterministic code (`YouTubeMarketSignalService`) | ❌ **FORBIDDEN** |
| **freshness** | Deterministic code (`YouTubeMarketSignalService`) | ❌ **FORBIDDEN** |
| **competition** | Deterministic code (`YouTubeMarketSignalService`) | ❌ **FORBIDDEN** |
| **views_per_day / view_count** | Deterministic API measurements | ❌ **FORBIDDEN** |
| **historical_fit** | Deterministic analytics code (`StrategyFeedbackLoop`) | ❌ **FORBIDDEN** |
| **supporting_video_ids** | Observed YouTube video IDs (server-validated) | ❌ Cannot invent IDs; unobserved IDs are stripped |
| **channel_fit** | Reasoning Model (`TopicEvaluator`) | ✅ Allowed (0–10 scale) |
| **angle / viewer_question** | Reasoning Model (`OpportunityHypothesis`) | ✅ Allowed |
| **editorial rationale** | Reasoning Model (`EditorialEvaluationOutput`) | ✅ Allowed |

> [!NOTE]
> **Provenance & Duration Handling**:
> - `supporting_video_ids`: Server-side provenance validation filters/strips any hallucinated/unobserved video IDs, keeping verified IDs.
> - `duration_seconds`: Video duration is parsed and recorded for observability; no arbitrary short-filtering is applied in this phase.

---

## 5. Quota Governance & Granular Buckets

YouTube Data API v3 enforces separate dedicated quota buckets:

1. **Search Queries Bucket**:
   - Dedicated daily limit: **100 calls/day**.
   - `search.list` consumes **1 call/unit** in this dedicated bucket.
   - Does **NOT** consume or drain the General Data API unit budget.
2. **Video Upload Bucket**:
   - Dedicated daily limit: **100 calls/day**.
   - `videos.insert` consumes **1 call/unit** in this dedicated bucket.
3. **General Data API Bucket**:
   - Default daily total: **10,000 units/day**.
   - `videos.list` consumes **1 unit/call** in this bucket.
   - Other ordinary endpoints (`channels.list`, `thumbnails.set`, etc.) consume from this bucket per method costs.

### Quota Accounting & HTTP Response Semantics
- **HTTP Response Accounting**: Once an HTTP response is received from YouTube (including 4xx/5xx responses such as 400, 401, 403, 500), local quota accounting records the request in SQLite (`quota_usage_records`) per documented YouTube quota semantics.
- **Transport / Network Failures**: Transport exceptions occurring before any HTTP response is returned (e.g. DNS failure, connection timeout, connection refused) do **NOT** record local quota usage, preventing false spend entries.
- **Standard Discovery Run Cost**:
  - Seed search (1–2 queries): 1–2 calls from the Search Queries bucket.
  - Candidate measurement (max 5 candidates): at most 5 calls from the Search Queries bucket.
  - Total Search Queries bucket spend: $\le 7$ calls out of 100/day.
  - Total General bucket spend: $\le 7$ units (`videos.list`) out of 10,000/day.
- **Pre-flight Enforcement**: `QuotaBudgetManager.ensure_budget(operation)` executes before each call. If remaining bucket quota is insufficient, execution halts with `InsufficientQuotaError` / `BLOCKED`. Dummy metrics are never returned.
- **Cache TTL**: Market snapshots are cached for **24 hours** in SQLite (`market_signal_snapshots`), preventing duplicate quota spend during identical candidate evaluations.

---

## 6. Confidence Gates & Durable Portfolio Records

- **Gate**: `MIN_VALID_VIDEO_SAMPLE = 5`.
- If a candidate query yields fewer than 5 valid video observations:
  - `confidence = "INSUFFICIENT_SIGNAL"`.
  - The candidate is strictly excluded from batch-relative demand normalization and cannot alter viable candidate scores.
- **Portfolio Winner Selection**:
  - Only candidates with `confidence == "HIGH"` are eligible for `selected_topic`.
  - If all candidates fail the evidence threshold:
    - `selected_topic = None`
    - `selection_reason = "No candidate met minimum market evidence threshold (MIN_VALID_VIDEO_SAMPLE=5). All candidates marked INSUFFICIENT_SIGNAL."`
    - Upstream automation becomes `BLOCKED` instead of guessing an arbitrary winner.
- **Process-Restart Durability (Schema v6)**:
  - Complete portfolio decisions (including candidates, scores, rank, angles, winner, selection reason, and referenced `market_signal_ids`) are persisted to SQLite table `opportunity_portfolios`.
  - When reloaded via `repository.get_opportunity_portfolio(batch_id)`, the exact decision and its supporting `MarketSignalSnapshot` records can be fully reconstructed.
  - Reusing a cached snapshot from an earlier batch preserves the original snapshot ownership; the new portfolio references the cached snapshot ID without duplicate snapshot records.
