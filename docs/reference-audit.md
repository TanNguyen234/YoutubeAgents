# YouTube Autopilot — Reference Repositories Deep Audit Report

This report presents a thorough technical inspection of six reference repositories audited in Phase 1. Each repository was analyzed directly from source code covering architecture, pinned commit SHA, licensing, YouTube integrations, media rendering, error handling, state tracking, and security.

---

## 1. Pinned Repository Audit Matrix

| Repository | Branch | Audited Commit SHA | Audited At (ISO-8601) | License at Commit | Primary Language |
|---|---|---|---|---|---|
| **darkzOGx/youtube-automation-agent** | `master` | `030fd30e12150b4c793868acd04d4eeb5281e602` | `2026-08-15T19:05:00+07:00` | MIT | JavaScript (Node.js) |
| **khaoss85/youtube-autopilot** | `main` | `69d8f0cf2872bd1467b4d09d12eb1109603345e7` | `2026-08-15T19:05:00+07:00` | Proprietary / Unlicensed | Python 3.11 |
| **harry0703/MoneyPrinterTurbo** | `main` | `1f9f19c2021a68d04df228f33e9099a0c947f6f8` | `2026-08-15T19:05:00+07:00` | MIT | Python 3.11 |
| **ChaitanyaEswarRajeshJakki/gemini-youtube-automation** | `main` | `ce08cb7b64ef45df944a65d8b44b04bd9fc753db` | `2026-08-15T19:05:00+07:00` | MIT | Python 3.11 |
| **SaarD00/AI-Youtube-Shorts-Generator** | `Main` | `c1b0c84fdd457f74183e4253719597edb580d7ca` | `2026-08-15T19:05:00+07:00` | MIT | Python 3.10+ |
| **Mrshahidali420/youtube-shorts-automation** | `master` | `48cd3ece3e9974d74b917ee7eddc4cadc24efe13` | `2026-08-15T19:05:00+07:00` | MIT | Python 3.10+ |

---

## 2. Evidence-Based Repository Audits

### 2.1. darkzOGx/youtube-automation-agent

- **Repository**: `darkzOGx/youtube-automation-agent` (Branch: `master`, Commit: `030fd30e12150b4c793868acd04d4eeb5281e602`)
- **License**: MIT License (`LICENSE` present).
- **Core Dependencies**: `googleapis` (v173.0.0), `@google/genai` (v2.9.0), `sqlite3` (v5.1.6), `express` (v4.18.2), `microsoft-cognitiveservices-speech-sdk`, `playwright`, `replicate`.
- **Main Entrypoint**: `index.js`, `modern-auth.js`, `schedules/daily-automation.js`.
- **Architecture & Modules Inspected**:
  - `modern-auth.js`: Implements OAuth 2.0 web flow with dynamic local HTTP callback server on random high port ($8000 + \text{rand}(1000)$).
  - `agents/publishing-scheduling-agent.js`: Handles video insertion via YouTube Data API v3 (`youtube.videos.insert`), thumbnail upload (`youtube.thumbnails.set`), scheduled publish queue, and metadata tagging.
  - `agents/analytics-optimization-agent.js`: Interfaces with YouTube Analytics API v2 (`google.youtubeAnalytics({ version: 'v2' })`) to query video watch time, retention, and CTR.
  - `database/db.js`: SQLite schema for content strategies, scripts, thumbnails, SEO metadata, productions, schedules, and analytics reports.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN` (No live upstream execution conducted).
- **Known Upstream Issues & Risks**:
  - Historical upstream bug in v2.4 (`Content generation failed: topic must be a string`) fixed in early August 2026.
  - Entire codebase is Node.js/JavaScript; project control plane is Python 3.11+.
  - No automated test suite (only a single `test.js` script with manual console logging).
  - Uses raw commercial LLM API wrappers (`@google/genai`, `openai`, `replicate`).
- **Audit Conclusion**:
  - Adapt OAuth 2.0 server callback flow and YouTube Data/Analytics API patterns into native Python (`app/youtube/`). Reimplement SQLite database schema in Python dataclasses. Reject Node.js runtime and direct LLM API dependencies.

---

### 2.2. khaoss85/youtube-autopilot

- **Repository**: `khaoss85/youtube-autopilot` (Branch: `main`, Commit: `69d8f0cf2872bd1467b4d09d12eb1109603345e7`)
- **License**: Proprietary / Unlicensed (No top-level LICENSE file; header notices).
- **Core Dependencies**: `pydantic>=2.0`, `requests`, `python-dotenv`.
- **Main Entrypoint**: `run.py` (58KB script), `yt_autopilot/pipeline/build_video_package.py` (107KB script).
- **Architecture & Modules Inspected**:
  - `yt_autopilot/core/agent_coordinator.py`: Centralized coordinator managing multi-agent pipeline with unified `AgentContext`, `AgentCallRecord` performance timing, retry count limits, and structured `AgentError` hierarchy.
  - `yt_autopilot/core/schemas.py`: Formal Pydantic schemas for `TrendCandidate`, `SeriesFormat`, `SeriesSegment`, `VideoPlan`, `VideoScript`, `Timeline`, and `EditorialDecision`.
  - `yt_autopilot/core/pipeline_validator.py` & `monetization_qa.py`: Multi-stage quality gates, duration sanity checks, and revenue optimization heuristics.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`.
- **Known Upstream Issues & Risks**:
  - Monolithic 100KB+ files mixing prompt construction, file IO, and pipeline orchestration.
  - Direct embedding of Claude/Sora 2 commercial API prompt templates in agent classes.
  - Hardcoded Italian language/region defaults (`language="it"`, `region="IT"`).
  - Backup files (`build_video_package.py.bak`) committed directly in repository tree.
- **Audit Conclusion**:
  - Clean room reimplementation of the Pydantic schema contracts (`TrendCandidate`, `VideoPlan`, `VideoScript`, `VisualPlan`, `Timeline`) and `AgentCoordinator` patterns in `app/core/`. Reject monolithic scripts and external commercial video model wrappers.

---

### 2.3. harry0703/MoneyPrinterTurbo

- **Repository**: `harry0703/MoneyPrinterTurbo` (Branch: `main`, Commit: `1f9f19c2021a68d04df228f33e9099a0c947f6f8`)
- **License**: MIT License.
- **Core Dependencies**: `moviepy`, `ffmpeg-python`, `edge-tts`, `openai-whisper`, `fastapi`, `pillow`, `loguru`.
- **Main Entrypoint**: `main.py`, `app/services/video.py`, `app/services/voice.py`, `app/services/material.py`.
- **Architecture & Modules Inspected**:
  - `app/services/video.py`: Assembles video materials, manages subclips (`SubClippedVideoClip`), implements frame-rounding duration safety margins (`_VIDEO_DURATION_SAFETY_MARGIN = 0.1`), hardware encoder auto-detection (`h264_nvenc`, `h264_amf`, `h264_qsv`), BGM mixing, and subtitle overlay.
  - `app/services/voice.py`: Edge-TTS asynchronous audio generation with custom pitch/rate controls.
  - `app/services/subtitle.py`: Word-level timestamp alignment and ASS/SRT caption generation.
  - `app/services/material.py`: Stock footage downloading from Pexels and Pixabay APIs.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`.
- **Known Upstream Issues & Risks**:
  - Heavy RAM usage and object accumulation during batch rendering with MoviePy.
  - Monolithic WebUI and generic LLM prompt-to-video architecture without multi-hop fact verification or channel memory.
- **Audit Conclusion**:
  - **FFmpeg patterns**: `ADAPT` (hardware acceleration detection, frame rounding safety margins).
  - **Edge-TTS**: `ADAPT` (voice synthesis in `app/tts/`).
  - **Subtitle timing**: `ADAPT` (word-level timestamps and ASS/SRT formatting).
  - **Stock media provider pattern**: `ADAPT / REIMPLEMENT` (in `app/media/` with mandatory asset provenance).
  - **WebUI**: `REJECT`.
  - **LLM generation**: `REJECT`.
  - **Whole sidecar**: `DEFER` (prioritize native modular Python implementation).

---

### 2.4. ChaitanyaEswarRajeshJakki/gemini-youtube-automation

- **Repository**: `ChaitanyaEswarRajeshJakki/gemini-youtube-automation` (Branch: `main`, Commit: `ce08cb7b64ef45df944a65d8b44b04bd9fc753db`)
- **License**: MIT License.
- **Core Dependencies**: `google-api-python-client`, `google-auth-oauthlib`, `moviepy`, `gTTS`, `pillow`, `requests`.
- **Main Entrypoint**: `main.py`, `src/uploader.py`, `src/generator.py`.
- **Architecture & Modules Inspected**:
  - `src/uploader.py`: Concise Python implementation of Google OAuth 2.0 (`InstalledAppFlow`, token caching in `credentials.json`, automatic refresh via `Request()`) and resumable upload using `MediaFileUpload(..., resumable=True)`.
  - `.github/workflows/main.yml`: Scheduled GitHub Actions cron executing the pipeline daily.
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`.
- **Known Upstream Issues & Risks**:
  - Hardcoded `privacyStatus: 'public'` by default (violates our private-by-default safety rule).
  - Direct dependency on legacy `google-generativeai` SDK without schema constraints.
  - Zero validation before upload.
- **Audit Conclusion**:
  - Adapt Python `googleapiclient` + `google_auth_oauthlib` resumable upload snippet into `app/youtube/uploader.py` with mandatory `privacyStatus = 'private'` override. Reject direct Gemini LLM generation and MoviePy renderer.

---

### 2.5. SaarD00/AI-Youtube-Shorts-Generator

- **Repository**: `SaarD00/AI-Youtube-Shorts-Generator` (Branch: `Main`, Commit: `c1b0c84fdd457f74183e4253719597edb580d7ca`)
- **License**: MIT License.
- **Core Dependencies**: `ffmpeg-python`, `google-generativeai`, `requests`.
- **Main Entrypoint**: `main.py`, `modules/composer.py`, `modules/audio.py`.
- **Architecture & Modules Inspected**:
  - `modules/composer.py`: Direct FFmpeg filter composition for vertical 9:16 videos (`scale=1080:1920:force_original_aspect_ratio=increase`, `crop=1080:1920`, `fps=30`), scene concatenation, and Windows Media Player YUV420p color space compatibility (`pix_fmt='yuv420p'`).
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`.
- **Known Upstream Issues & Risks**:
  - Hardcoded local asset paths and absence of configuration abstractions.
  - Ad-hoc scripts with no formal testing.
- **Audit Conclusion**:
  - Adapt the 9:16 vertical FFmpeg filter chain and YUV420p color space normalization into `app/render/ffmpeg_engine.py`. Reject ad-hoc Gemini generation and avatar looping scripts.

---

### 2.6. Mrshahidali420/youtube-shorts-automation

- **Repository**: `Mrshahidali420/youtube-shorts-automation` (Branch: `master`, Commit: `48cd3ece3e9974d74b917ee7eddc4cadc24efe13`)
- **License**: MIT License.
- **Core Dependencies**: `yt-dlp`, `openpyxl`, `google-api-python-client`, `selenium`.
- **Main Entrypoint**: `youtube_shorts/downloader_channel.py`, `youtube_shorts/performance_tracker.py`.
- **Architecture & Modules Inspected**:
  - `youtube_shorts/youtube_limits.py`: YouTube API quota calculation and rate limiting heuristics.
  - `youtube_shorts/performance_tracker.py`: Periodic queries to YouTube Data API for video statistics (view counts, likes, comments).
- **Implementation Status**: `STATICALLY_INSPECTED`
- **Live Verification**: `NOT RUN`.
- **Critical Violations & Anti-patterns**:
  - **Plagiarism Pipeline**: `downloader_channel.py` and `downloader_keyword.py` rip other creators' videos using `yt_dlp` for automated reupload. **STRICTLY FORBIDDEN & REJECTED**.
  - Uses Excel files (`.xlsx` via `openpyxl`) as a makeshift database instead of SQLite.
  - Selenium browser automation used for uploads instead of verified API calls.
- **Audit Conclusion**:
  - Reject all scrapers, downloading, Excel persistence, and Selenium automation. Adapt only the YouTube API quota formulas and performance tracking queries into `app/youtube/quota.py` and `app/analytics/tracker.py`.
