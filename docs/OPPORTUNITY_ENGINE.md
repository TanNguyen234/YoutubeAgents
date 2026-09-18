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
         │     └─► 100 quota units per call
         │
         ├─► YouTube videos.list (Statistics, ContentDetails, Snippet)
         │     └─► 1 quota unit per call
         │
         ├─► Normalize MarketVideoObservation:
         │     [video_id, title, channel_id, published_at, view_count, duration]
         │
         └─► Calculate Derived Metrics & Persist MarketSignalSnapshot (Schema v5)
  ```

---

## 3. Deterministic Proxy Formulas (Formula Version: `v1.0`)

For each video $i$ in a sample of size $N \ge 1$:
$$\text{age\_days}_i = \max\left(\frac{\text{collected\_at} - \text{published\_at}_i}{86400}, 0.5\right)$$
$$\text{views\_per\_day}_i = \frac{\text{view\_count}_i}{\text{age\_days}_i}$$

### 1. Demand Proxy (0.0 – 10.0 scale)
Combines median and 75th-percentile velocity to dampen extreme viral outliers while rewarding strong baseline velocity:
$$\text{raw\_demand} = 0.70 \times \text{median}(\text{views\_per\_day}) + 0.30 \times P_{75}(\text{views\_per\_day})$$
Normalized batch-relatively across candidates in the portfolio:
$$\text{Demand Score} = 3.0 + 7.0 \times \frac{\text{raw\_demand} - \min(\text{raw\_demand})}{\max(\text{raw\_demand}) - \min(\text{raw\_demand})}$$
*(For single candidates or identical velocities, logarithmic damping is applied: $\min(10.0, \max(1.0, \log_{10}(\text{raw\_demand}) \times 2.0))$.)*

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
| **supporting_video_ids** | Observed YouTube video IDs (server-validated) | ❌ Cannot invent new IDs |
| **channel_fit** | Reasoning Model (`TopicEvaluator`) | ✅ Allowed (0–10 scale) |
| **angle / viewer_question** | Reasoning Model (`OpportunityHypothesis`) | ✅ Allowed |
| **editorial rationale** | Reasoning Model (`EditorialEvaluationOutput`) | ✅ Allowed |

---

## 5. Quota Governance & Economics

- `search.list` cost: **100 units**
- `videos.list` cost: **1 unit**
- **Default Budget per Discovery Run**:
  - 1–2 seed queries: $2 \times 100 = 200$ units.
  - 1–2 video batches: $2 \times 1 = 2$ units.
  - Candidate specific measurements (max 5 candidates): $5 \times 101 = 505$ units.
  - Total standard discovery cycle: $\approx 707$ units (well within daily free quota of 10,000 units).
- **Pre-flight Quota Checks**: `QuotaBudgetManager.ensure_budget()` executes before any external API request. If remaining quota is insufficient, execution fails explicitly with `InsufficientQuotaError` / `BLOCKED`. Dummy metrics are never returned.
- **Cache TTL**: Market snapshots are cached for **24 hours** in SQLite (`market_signal_snapshots`), preventing duplicate quota spend during identical candidate evaluations.

---

## 6. Confidence Gates & Insufficient Signal Handling

- **Gate**: `MIN_VALID_VIDEO_SAMPLE = 5`.
- If a candidate query yields fewer than 5 valid video observations:
  - `confidence = "INSUFFICIENT_SIGNAL"`
  - The candidate is flagged as unverified.
- **Portfolio Winner Selection**:
  - Only candidates with `confidence == "HIGH"` are eligible for `selected_topic`.
  - If all candidates fail the evidence threshold:
    - `selected_topic = None`
    - `selection_reason = "No candidate met minimum market evidence threshold (MIN_VALID_VIDEO_SAMPLE=5). All candidates marked INSUFFICIENT_SIGNAL."`
    - Upstream automation becomes `BLOCKED` instead of guessing an arbitrary winner.
