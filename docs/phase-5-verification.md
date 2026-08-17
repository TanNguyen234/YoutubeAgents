# Phase 5 — Real Media Factory, TTS, Subtitles, FFmpeg Rendering & Technical QA Verification Report

**Phase Status**: `VERIFIED & CLOSED` (Phase 5.0.2 Complete)  
**Execution Environment**: Antigravity Runtime + SDK CLI (`agy`)  
**Media Stack**: Real EdgeTTS (`edge-tts`) + Pillow (`PIL`) + FFmpeg 8.0.1 + FFprobe 8.0.1  
**Audio Standards**: EBU R128 Integrated Loudness Normalization (`-14.0 LUFS` target, $\pm 1.5\text{ LU}$ tolerance envelope: $[-15.5, -12.5]\text{ LUFS}$)  
**Video Standards**: 9:16 Vertical Video (`1080x1920`), `30.0 fps`, `libx264` (`yuv420p`), `aac` stereo audio  
**Auditable Manifest**: [`docs/evaluation/phase5_manifest.json`](file:///d:/Download/YoutubeAgents/docs/evaluation/phase5_manifest.json)  
**Source Commit**: `4d0b8eb2aea5d2e2aaf838859328f2aa0d6da1df`  
**Date**: 2026-08-17  

---

## 1. Executive Summary & Architectural Invariants

In Phase 5 (including Phase 5.0.1 QA Hardening and Phase 5.0.2 Lifecycle & Rerender Contract), the YouTube Autopilot pipeline established the real local media rendering plane, implementing deterministic contracts, robust lifecycle state transitions, and live media generation:

1. **Strict Canonical Narration Provenance**:
   - Spoken narration is bound exclusively to `script.get_canonical_narration()`.
   - Complete SHA-256 hash equality is asserted across all stages:
     $$\text{canonical\_narration\_sha256} == \text{tts\_input\_sha256} == \text{subtitle\_source\_sha256} == \text{render\_input\_narration\_sha256}$$
   - No silent modifications, summarizations, translations, or omissions occurred.

2. **Per-Scene Normalized Encoding**:
   - Each scene card PNG (rendered at $1080 \times 1920$ with responsive typography and high-contrast color palettes) is pre-encoded into an isolated, normalized video segment (`scene_00.mp4`, `scene_01.mp4`, ...) using `libx264`, `yuv420p`, `-threads 2`, `-tune stillimage`, and standard timebases.
   - Normalized segments are concatenated via FFmpeg concat demuxer before muxing with audio and subtitles, eliminating timebase desynchronization bugs.

3. **Master EBU R128 Loudness Verification & Fail-Closed QA**:
   - Audio is normalized during rendering via FFmpeg `loudnorm` filter (`I=-14:LRA=11:TP=-1.5`).
   - The **final rendered MP4 file** is measured via EBU R128 analysis (`-af loudnorm=print_format=json -f null -`), verifying that the final master achieves **-15.23 LUFS** (well within the $-15.5$ to $-12.5$ LUFS target envelope).
   - Loudness measurement is strictly fail-closed: any subprocess error, missing audio, or JSON parse failure raises `MediaQAError` / marks `QualityStatus.FAILED` with descriptive issues; `-14.0 LUFS` is never fabricated. Non-existent files return sentinel `-999.0 LUFS`.

4. **Production Fingerprint & Real Same-Project Cache Invalidation (Phase 5.0.2)**:
   - Render configurations and asset inputs generate a deterministic 64-character SHA-256 production fingerprint:
     $$\text{fingerprint} = \text{SHA256}(\text{narration\_sha256} + \text{render\_profile} + \text{tts\_backend} + \text{voice} + \text{rate} + \text{pitch} + \text{sub\_config} + \sum \text{scene\_hashes})$$
   - `requested_production_fingerprint` is computed before any cache reuse decision; `cached_manifest.production_fingerprint == requested_production_fingerprint` is strictly mandatory.
   - **Same-Project Rerender Contract**: Allows media-only parameter changes (voice, rate, pitch, render profile) on projects in `READY_FOR_REVIEW` or `QA_FAILED`. On cache miss or parameter change, the state machine transitions:
     $$\text{READY\_FOR\_REVIEW} \longrightarrow \text{PRODUCING} \longrightarrow \text{RENDERED} \longrightarrow \text{READY\_FOR\_REVIEW}$$
   - When the fingerprint matches, it returns the cached result without re-synthesizing audio or re-rendering video.

5. **Typed Lifecycle & Capability Preflight Semantics**:
   - Pre-flight capability checks verify `ffmpeg`, `ffprobe`, `edge-tts`, `Pillow`, and directory writability.
   - Missing capabilities or environmental TTS network failures cleanly transition projects to `BLOCKED` (`MediaProductionBlockerError`, `TTSBlockerError`).
   - Rendering/subprocess crashes transition projects to `FAILED`.
   - Technical QA failures transition projects to `QA_FAILED`.
   - Valid renders transition cleanly: `VERIFIED -> PRODUCING -> RENDERED -> READY_FOR_REVIEW`.

6. **Deterministic vs. Live Test Suite Separation**:
   - `tests/media/test_tts.py`: 100% deterministic unit/contract tests with fake doubles (no network calls).
   - `tests/media/test_tts_live.py`: Real live EdgeTTS network test marked with `@pytest.mark.live`.

---

## 2. REAL Live Execution Evidence (`scripts/run_real_phase5.py`)

Executed on SQLite database with live EdgeTTS and FFmpeg:

| Metric / Dimension | Specification / Target | Measured Real Value | Status |
| :--- | :--- | :--- | :--- |
| **Topic** | Mastering SQLite WAL Mode Concurrency | SQLite WAL Mode Concurrency | `MATCH` |
| **Canonical Narration SHA-256** | 64-char hex hash | `42233fefc65fd01394e440ec3957c23223e2ed33e630988dcbf1c4e389354d9f` | `VERIFIED` |
| **Narration Provenance** | $\text{Canonical} == \text{TTS} == \text{Subtitles} == \text{Render}$ | `42233fef...` across all 4 boundaries | `VERIFIED` |
| **TTS Engine & Voice** | EdgeTTS / `en-US-GuyNeural` | `edge-tts` (`en-US-GuyNeural`, Rate: `+0%`, Pitch: `+0Hz`) | `REAL` |
| **Synthesized Audio Duration** | Spoken narration duration | `32.09s` (Audio SHA: `59eacf2d...`) | `VERIFIED` |
| **Subtitles** | Synchronized SRT cues | 18 cues (SHA: `acd12074...`) | `VERIFIED` |
| **Rendered Video Resolution** | $1080 \times 1920$ (9:16 Vertical) | $1080 \times 1920$ | `PASS` |
| **Video Frame Rate** | 30.0 fps constant ($\pm 0.5\text{ fps}$) | 30.0 fps | `PASS` |
| **Video / Audio Codecs** | H.264 / AAC | `h264` (`yuv420p`) / `aac` stereo | `PASS` |
| **Final Video Duration** | Equal to audio duration ($\pm 0.5\text{s}$) | Video: `32.10s`, Audio: `32.09s` (Drift: `0.012s`) | `PASS` |
| **Final Master Loudness** | Target: -14.0 LUFS ($\pm 1.5\text{ LU}$, $[-15.5, -12.5]$ LUFS) | **-15.23 LUFS** | `PASS` |
| **Rendered MP4 File Size** | Valid non-empty media file | `2,363,052 bytes` (~2.36 MB) | `PASS` |
| **Rendered MP4 SHA-256** | Final video content hash | `36c482b5edfed37c19ed826ff3a3f208ebd3f2006ccc4c053ca71fe8b87ac8dc` | `AUDITABLE` |
| **Production Fingerprint** | Unique combination digest | `c9d4b3a15646241fcdc04afb3aa5c2386c31b8103fca829bddce1a04cfdaa8d6` | `AUDITABLE` |
| **Technical QA Verdict** | All checks pass | `PASSED` (0 issues) | `PASS` |
| **Final Lifecycle State** | `READY_FOR_REVIEW` | `READY_FOR_REVIEW` | `PASS` |

---

## 3. Real Render Artifacts

All rendered media assets were persisted locally and logged in the database:
- **Audio Voiceover**: `output/projects/proj-sqlite-wal-01/audio/voiceover_proj-sqlite-wal-01.mp3`
- **Subtitles**: `output/projects/proj-sqlite-wal-01/subtitles/subtitles_proj-sqlite-wal-01.srt`
- **Scene Cards**:
  - Scene 0: `output/projects/proj-sqlite-wal-01/scenes/scene_00.png`
  - Scene 1: `output/projects/proj-sqlite-wal-01/scenes/scene_01.png`
  - Scene 2: `output/projects/proj-sqlite-wal-01/scenes/scene_02.png`
- **Final Master MP4**: `output/projects/proj-sqlite-wal-01/render/final_proj-sqlite-wal-01.mp4`
- **Render Manifest**: `output/projects/proj-sqlite-wal-01/manifests/render_manifest.json`

---

## 4. Test Suite Execution Summary

### Deterministic Test Suite Gate (`pytest -v -m "not live"`)
```bash
$ pytest -v -m "not live"
================ 117 passed, 2 deselected in 98.18s (0:01:38) =================
```
- **Database & Migrations**: 13/13 PASSED
- **Domain Models & Lifecycle State Machine**: 16/16 PASSED (including `test_ready_for_review_to_producing_media_rerender`)
- **Research, Topic Strategy & Grounding**: 30/30 PASSED
- **Media Pipeline & Contracts (`tests/media/`)**: 42/42 PASSED
  - `test_canonical_binding.py`: 4/4 PASSED
  - `test_capabilities.py`: 2/2 PASSED
  - `test_ffmpeg_renderer.py`: 3/3 PASSED
  - `test_media_pipeline.py`: 12/12 PASSED (including true same-project voice/rate/pitch/profile/scene cache invalidation and cache reuse tests)
  - `test_media_qa.py`: 14/14 PASSED (fail-closed loudness, codec, pixel format, FPS, drift, missing stream rejections)
  - `test_scene_planning.py`: 2/2 PASSED
  - `test_subtitles.py`: 2/2 PASSED
  - `test_tts.py`: 3/3 PASSED
- **Smoke Tests**: 4/4 PASSED

### Live Test Gate (`pytest -v tests/media/test_tts_live.py`)
```bash
$ pytest -v tests/media/test_tts_live.py
============================== 1 passed in 3.79s ==============================
```

---

## 5. Phase 5 Sign-Off & Invariants

- [x] Spoken narration is bound exclusively to `script.get_canonical_narration()` with 100% hash parity.
- [x] Real EdgeTTS audio synthesis with rate and pitch controls.
- [x] Responsive 9:16 visual scene cards with Pillow and dynamic typography.
- [x] Per-scene normalized encoding and FFmpeg concat demuxer composition.
- [x] Subtitles burned with styled ASS / SRT format.
- [x] Measured final master loudness at -15.23 LUFS via EBU R128 (target -14.0 $\pm 1.5$ LU).
- [x] Fail-closed loudness parsing and strict technical QA gate assertions.
- [x] Idempotency enforced via mandatory `production_fingerprint` match prior to reuse.
- [x] Same-project media rerenders allowed from `READY_FOR_REVIEW` or `QA_FAILED` with full FSM history.
- [x] All database records (`Asset`, `QualityResult`, `VideoProject`) persisted.
- [x] State transitions strictly enforce `VERIFIED -> PRODUCING -> RENDERED -> READY_FOR_REVIEW`.
- [x] Sanitized commit-safe manifest generated at `docs/evaluation/phase5_manifest.json`.
- [x] **STOPPED before Phase 6** (no YouTube OAuth/upload/scheduling/analytics).
