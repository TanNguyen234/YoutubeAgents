# YouTube Autopilot — Reference Architectural Decisions

This document records the definitive architectural decisions (`REUSE`, `ADAPT`, `REIMPLEMENT`, `REJECT`, `DEFER`) for all audited features across the six pinned reference repositories.

---

## Decision Matrix Summary

| Repository | Pinned Commit SHA | Feature / Component | License | Decision | Target Location / Replacement |
|---|---|---|---|---|---|
| `darkzOGx/youtube-automation-agent` | `030fd30e12150b4c793868acd04d4eeb5281e602` | YouTube OAuth Local Server Flow | MIT | **ADAPT** | `app/youtube/auth.py` |
| `darkzOGx/youtube-automation-agent` | `030fd30e12150b4c793868acd04d4eeb5281e602` | YouTube Data API v3 Video & Thumbnail Upload | MIT | **ADAPT** | `app/youtube/uploader.py` |
| `darkzOGx/youtube-automation-agent` | `030fd30e12150b4c793868acd04d4eeb5281e602` | YouTube Analytics API v2 Querying | MIT | **ADAPT** | `app/analytics/tracker.py` |
| `darkzOGx/youtube-automation-agent` | `030fd30e12150b4c793868acd04d4eeb5281e602` | SQLite Database Schema | MIT | **REIMPLEMENT** | `app/db/schema.py` & `app/db/repository.py` |
| `darkzOGx/youtube-automation-agent` | `030fd30e12150b4c793868acd04d4eeb5281e602` | Node.js Runtime & Third-Party LLM Wrappers | MIT | **REJECT** | Antigravity Native Reasoning Engine |
| `khaoss85/youtube-autopilot` | `69d8f0cf2872bd1467b4d09d12eb1109603345e7` | Typed Pydantic Schema Contracts | Proprietary | **REIMPLEMENT** | `app/core/schemas.py` |
| `khaoss85/youtube-autopilot` | `69d8f0cf2872bd1467b4d09d12eb1109603345e7` | AgentCoordinator & Context Propagation | Proprietary | **REIMPLEMENT** | `app/core/coordinator.py` |
| `khaoss85/youtube-autopilot` | `69d8f0cf2872bd1467b4d09d12eb1109603345e7` | Multi-Stage Pipeline Validation Gates | Proprietary | **REIMPLEMENT** | `app/core/validators.py` |
| `khaoss85/youtube-autopilot` | `69d8f0cf2872bd1467b4d09d12eb1109603345e7` | 100KB Monolithic Files & Hardcoded Claude Prompts | Proprietary | **REJECT** | Modular Python Services |
| `harry0703/MoneyPrinterTurbo` | `1f9f19c2021a68d04df228f33e9099a0c947f6f8` | Hardware Encoder Detection & Duration Margin | MIT | **ADAPT** | `app/render/ffmpeg_engine.py` |
| `harry0703/MoneyPrinterTurbo` | `1f9f19c2021a68d04df228f33e9099a0c947f6f8` | Edge-TTS Voice Synthesis | MIT | **ADAPT** | `app/tts/edge_service.py` |
| `harry0703/MoneyPrinterTurbo` | `1f9f19c2021a68d04df228f33e9099a0c947f6f8` | Word-Level Subtitle Timestamp Alignment | MIT | **ADAPT** | `app/render/subtitles.py` |
| `harry0703/MoneyPrinterTurbo` | `1f9f19c2021a68d04df228f33e9099a0c947f6f8` | Stock Media Provider Pattern (Pexels/Pixabay) | MIT | **ADAPT / REIMPLEMENT** | `app/media/provider.py` (with Provenance) |
| `harry0703/MoneyPrinterTurbo` | `1f9f19c2021a68d04df228f33e9099a0c947f6f8` | WebUI Frontend & Direct Prompt Generation | MIT | **REJECT** | CLI & Antigravity Control Plane |
| `harry0703/MoneyPrinterTurbo` | `1f9f19c2021a68d04df228f33e9099a0c947f6f8` | Whole Standalone Media Sidecar Architecture | MIT | **DEFER** | Native Python Services First |
| `ChaitanyaEswarRajeshJakki/gemini-youtube-automation` | `ce08cb7b64ef45df944a65d8b44b04bd9fc753db` | Python Google OAuth2 & Resumable Upload | MIT | **ADAPT** | `app/youtube/uploader.py` |
| `ChaitanyaEswarRajeshJakki/gemini-youtube-automation` | `ce08cb7b64ef45df944a65d8b44b04bd9fc753db` | Hardcoded Public Upload & Gemini Script Gen | MIT | **REJECT** | Private-by-default uploader & Antigravity control plane |
| `SaarD00/AI-Youtube-Shorts-Generator` | `c1b0c84fdd457f74183e4253719597edb580d7ca` | 9:16 Vertical FFmpeg Filters & Scene Stitching | MIT | **ADAPT** | `app/render/ffmpeg_engine.py` |
| `SaarD00/AI-Youtube-Shorts-Generator` | `c1b0c84fdd457f74183e4253719597edb580d7ca` | Ad-hoc Script Generation & Avatar Loop Hacks | MIT | **REJECT** | Structured Storyboard & Visual Planner |
| `Mrshahidali420/youtube-shorts-automation` | `48cd3ece3e9974d74b917ee7eddc4cadc24efe13` | YouTube API Quota Tracking & Metric Querying | MIT | **ADAPT** | `app/youtube/quota.py` & `app/analytics/tracker.py` |
| `Mrshahidali420/youtube-shorts-automation` | `48cd3ece3e9974d74b917ee7eddc4cadc24efe13` | yt-dlp Video Scraping / Reuploading Pipeline | MIT | **REJECT (BANNED)** | **BANNED**: Strict originality & provenance rules |
| `Mrshahidali420/youtube-shorts-automation` | `48cd3ece3e9974d74b917ee7eddc4cadc24efe13` | Excel-based Storage & Selenium Web Automation | MIT | **REJECT** | SQLite local persistence & Official YouTube REST API |

---

## Detailed Decision Specifications

### 1. darkzOGx/youtube-automation-agent
- **Repository**: `darkzOGx/youtube-automation-agent` (Commit: `030fd30e12150b4c793868acd04d4eeb5281e602`, Audited: `2026-08-15T19:05:00+07:00`)

#### 1.1. YouTube OAuth 2.0 Flow
- **Source File**: `modern-auth.js`
- **Feature**: Temporary HTTP server for OAuth callback on dynamic high port.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Fixed 5-minute timeout; requires local browser.
- **License**: MIT.
- **Decision**: **ADAPT**
- **Reason**: Proven local authorization code reception pattern.
- **Target Replacement**: `app/youtube/auth.py` using Python `google_auth_oauthlib.flow.InstalledAppFlow`.

#### 1.2. YouTube Data API v3 Video & Thumbnail Upload
- **Source File**: `agents/publishing-scheduling-agent.js`
- **Feature**: Multipart video upload, thumbnail association, scheduled publish time, and simulation check.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: JavaScript stream handling differences from Python IO.
- **License**: MIT.
- **Decision**: **ADAPT**
- **Reason**: Clear API parameter mapping (`snippet`, `status`, `thumbnails.set`).
- **Target Replacement**: `app/youtube/uploader.py` using `googleapiclient.http.MediaFileUpload` with resumable chunking.

#### 1.3. YouTube Analytics API v2 Querying
- **Source File**: `agents/analytics-optimization-agent.js`
- **Feature**: Metric queries for views, watch time, retention, CTR, and traffic sources.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Quota limits and delayed metric availability (up to 48 hours for full retention data).
- **License**: MIT.
- **Decision**: **ADAPT**
- **Reason**: Aligns with Stage 14 Analytics Tracking requirements.
- **Target Replacement**: `app/analytics/tracker.py` with typed analytics response schemas.

#### 1.4. SQLite Database Schema
- **Source File**: `database/db.js`
- **Feature**: Relational tables for strategies, scripts, thumbnails, SEO, productions, schedules, and analytics.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Lacks foreign key enforcement in some queries; raw SQL string queries in JS.
- **License**: MIT.
- **Decision**: **REIMPLEMENT**
- **Reason**: Need typed SQLite models with proper transactions and migrations in Python.
- **Target Replacement**: `app/db/schema.py` and `app/db/repository.py` using standard `sqlite3`.

---

### 2. khaoss85/youtube-autopilot
- **Repository**: `khaoss85/youtube-autopilot` (Commit: `69d8f0cf2872bd1467b4d09d12eb1109603345e7`, Audited: `2026-08-15T19:05:00+07:00`)

#### 2.1. Typed Pydantic Schema Contracts
- **Source File**: `yt_autopilot/core/schemas.py`
- **Feature**: Domain models for `TrendCandidate`, `SeriesFormat`, `VideoPlan`, `VideoScript`, `Timeline`, `VisualPlan`.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Tightly coupled to Italian niche templates.
- **License**: Proprietary / Unlicensed.
- **Decision**: **REIMPLEMENT**
- **Reason**: Structural domain contract is excellent; clean room reimplementation avoids licensing ambiguity.
- **Target Replacement**: `app/core/schemas.py` as clean room Python typed models.

#### 2.2. AgentCoordinator & Context Propagation
- **Source File**: `yt_autopilot/core/agent_coordinator.py`
- **Feature**: Central orchestration state machine, `AgentContext`, execution metrics tracking, and error retry classification.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Monolithic file structure (1500+ lines); hardcoded recovery fallbacks.
- **License**: Proprietary / Unlicensed.
- **Decision**: **REIMPLEMENT**
- **Reason**: Essential architectural pattern for managing transitions across our 15 stages without chaotic coupling.
- **Target Replacement**: `app/core/coordinator.py` with explicit runtime states (`REAL`, `TEST`, `DRY_RUN`, `BLOCKED`, `FAILED`).

#### 2.3. Pipeline Validation Gates
- **Source File**: `yt_autopilot/core/pipeline_validator.py`
- **Feature**: Multi-point sanity validation for script length, narration pacing, keyword saturation, and visual timeline continuity.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Complex regex matching that can reject valid non-standard formats.
- **License**: Proprietary / Unlicensed.
- **Decision**: **REIMPLEMENT**
- **Reason**: Mandatory quality gates to prevent broken assets from reaching render and upload stages.
- **Target Replacement**: `app/core/validators.py`.

---

### 3. harry0703/MoneyPrinterTurbo
- **Repository**: `harry0703/MoneyPrinterTurbo` (Commit: `1f9f19c2021a68d04df228f33e9099a0c947f6f8`, Audited: `2026-08-15T19:05:00+07:00`)

#### 3.1. FFmpeg Rendering Engine & Duration Safety Margins
- **Source File**: `app/services/video.py`
- **Feature**: Hardware acceleration auto-detection (`h264_nvenc`, `h264_qsv`, `h264_amf`), `_VIDEO_DURATION_SAFETY_MARGIN = 0.1` frame-rounding compensation, BGM audio ducking.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: MoviePy memory accumulation on batch runs.
- **License**: MIT.
- **Decision**: **ADAPT**
- **Reason**: Critical video engineering heuristics preventing black frames, audio truncation, and slow CPU rendering.
- **Target Replacement**: `app/render/ffmpeg_engine.py` (via direct FFmpeg subprocess).

#### 3.2. Edge-TTS Audio Synthesis & Subtitle Timestamps
- **Source File**: `app/services/voice.py`, `app/services/subtitle.py`
- **Feature**: Free, high-quality voice synthesis via Edge-TTS and word-level timestamp generation.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Network latency and rate limiting on excessive concurrent requests.
- **License**: MIT.
- **Decision**: **ADAPT**
- **Reason**: Standardized TTS generation for Stage 8 without paid API dependency.
- **Target Replacement**: `app/tts/edge_service.py` and `app/render/subtitles.py`.

#### 3.3. Stock Media Provider Pattern (Pexels / Pixabay)
- **Source File**: `app/services/material.py`
- **Feature**: Keyword-based stock image/video search and download from Pexels and Pixabay APIs.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Missing license provenance records in upstream implementation.
- **License**: MIT.
- **Decision**: **ADAPT / REIMPLEMENT**
- **Reason**: Stock media fetching is useful for B-roll generation, but must strictly record asset provenance (URL, license, hash, timestamp).
- **Target Replacement**: `app/media/provider.py` with mandatory asset provenance logging.

#### 3.4. WebUI & Standalone Sidecar Architecture
- **Source File**: `webui/`, `main.py`
- **Feature**: Streamlit/FastAPI WebUI and prompt-to-video monolithic generation.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Generic prompt generation without multi-stage validation or channel memory.
- **License**: MIT.
- **Decision**: **REJECT WebUI, DEFER Sidecar**
- **Reason**: WebUI is unnecessary for our agentic pipeline; sidecar architecture deferred in favor of native Python services.
- **Target Replacement**: Native modular services in `app/render/` and `app/tts/`.

---

### 4. ChaitanyaEswarRajeshJakki/gemini-youtube-automation
- **Repository**: `ChaitanyaEswarRajeshJakki/gemini-youtube-automation` (Commit: `ce08cb7b64ef45df944a65d8b44b04bd9fc753db`, Audited: `2026-08-15T19:05:00+07:00`)

#### 4.1. Python Google OAuth2 & Resumable Upload
- **Source File**: `src/uploader.py`
- **Feature**: Minimalist Python implementation of `InstalledAppFlow`, token caching, and `MediaFileUpload` resumable upload.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Hardcoded `privacyStatus: 'public'`.
- **License**: MIT.
- **Decision**: **ADAPT**
- **Reason**: Simple, robust Python standard implementation for YouTube v3 uploads.
- **Target Replacement**: `app/youtube/uploader.py` with default `privacyStatus = 'private'` safety lock.

---

### 5. SaarD00/AI-Youtube-Shorts-Generator
- **Repository**: `SaarD00/AI-Youtube-Shorts-Generator` (Commit: `c1b0c84fdd457f74183e4253719597edb580d7ca`, Audited: `2026-08-15T19:05:00+07:00`)

#### 5.1. 9:16 Vertical Video FFmpeg Filters
- **Source File**: `modules/composer.py`
- **Feature**: Vertical scale (`1080:1920`), center crop, 30fps normalization, and `yuv420p` pixel format for Windows compatibility.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Fixed hardcoded output dimensions; lacks dynamic layout presets.
- **License**: MIT.
- **Decision**: **ADAPT**
- **Reason**: Reliable FFmpeg filter chain for YouTube Shorts (9:16) rendering.
- **Target Replacement**: `app/render/ffmpeg_engine.py` under layout preset `ShortsPreset(width=1080, height=1920)`.

---

### 6. Mrshahidali420/youtube-shorts-automation
- **Repository**: `Mrshahidali420/youtube-shorts-automation` (Commit: `48cd3ece3e9974d74b917ee7eddc4cadc24efe13`, Audited: `2026-08-15T19:05:00+07:00`)

#### 6.1. YouTube API Quota Management & Performance Tracker
- **Source File**: `youtube_shorts/youtube_limits.py`, `youtube_shorts/performance_tracker.py`
- **Feature**: Daily 10,000 unit quota calculation and batch video statistics fetch.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Quota limits can change per GCP project; relies on Excel backups.
- **License**: MIT.
- **Decision**: **ADAPT**
- **Reason**: Essential for avoiding quota exhaustion during heavy video production/analytics cycles.
- **Target Replacement**: `app/youtube/quota.py` and `app/analytics/tracker.py`.

#### 6.2. Video Scraping & Reuploading Pipeline
- **Source File**: `youtube_shorts/downloader_channel.py`, `downloader_keyword.py`
- **Feature**: Downloads third-party videos via `yt-dlp` for automated reposting.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`
- **Known Upstream Issues/Risks**: Copyright strikes, channel termination, legal infringement.
- **License**: MIT.
- **Decision**: **REJECT (BANNED)**
- **Reason**: Violates our mandatory anti-plagiarism rule, copyright laws, and YouTube Terms of Service.
- **Target Replacement**: None. All content must be uniquely generated with full media provenance.
