# Phase 5 — Real Media Factory, TTS, Subtitles, FFmpeg Rendering & Technical QA Verification Report

**Phase Status**: `VERIFIED`  
**Execution Environment**: Antigravity Runtime + SDK CLI (`agy`)  
**Media Stack**: Real EdgeTTS (`edge-tts`) + Pillow (`PIL`) + FFmpeg 8.0.1 + FFprobe 8.0.1  
**Audio Standards**: EBU R128 Integrated Loudness Normalization (`-14.0 LUFS` target, $\pm 1.5\text{ LU}$ tolerance envelope: $[-15.5, -12.5]\text{ LUFS}$)  
**Video Standards**: 9:16 Vertical Video (`1080x1920`), `30.0 fps`, `libx264` (`yuv420p`), `aac` stereo audio  
**Auditable Manifest**: [`docs/evaluation/phase5_manifest.json`](file:///d:/Download/YoutubeAgents/docs/evaluation/phase5_manifest.json)  
**Source Commit**: `ed5bb5596f09b735bbd1cdc047d627c889519897`  
**Date**: 2026-08-17  

---

## 1. Executive Summary & Architectural Invariants

In Phase 5 (including Phase 5.0.1 Contract Closure Patch), the YouTube Autopilot pipeline established the real local media rendering plane, implementing deterministic contracts and live media generation:

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
   - The **final rendered MP4 file** is measured via EBU R128 analysis (`-af loudnorm=print_format=json -f null -`), verifying that the final master achieves **-14.96 LUFS** (well within the $-15.5$ to $-12.5$ LUFS target envelope).
   - Loudness measurement is strictly fail-closed: any subprocess error, missing audio, or JSON parse failure raises `MediaQAError` / marks `QualityStatus.FAILED` with descriptive issues; `-14.0 LUFS` is never fabricated.

4. **Production Fingerprint & Mandatory Cache Validation**:
   - Render configurations and asset inputs generate a deterministic 64-character SHA-256 production fingerprint:
     $$\text{fingerprint} = \text{SHA256}(\text{narration\_sha256} + \text{render\_profile} + \text{tts\_backend} + \text{voice} + \text{rate} + \text{pitch} + \text{sub\_config} + \sum \text{scene\_hashes})$$
   - `requested_production_fingerprint` is computed before any cache reuse decision; `cached_manifest.production_fingerprint == requested_production_fingerprint` is strictly mandatory. Any change in voice, speed rate, pitch, profile, or scene text invalidates the cache and forces a full re-render.

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

Executed on a fresh SQLite database (`data/real_phase5.db`) on clean commit `ed5bb55`.

| Metric / Dimension | Specification / Target | Measured Real Value | Status |
| :--- | :--- | :--- | :--- |
| **Topic** | Mastering SQLite WAL Mode Concurrency | SQLite WAL Mode Concurrency | `MATCH` |
| **Source Commit** | Git HEAD commit | `ed5bb5596f09b735bbd1cdc047d627c889519897` | `MATCH` |
| **Canonical Narration SHA-256** | 64-char hex hash | `26bf08d3fd066ca468324e435734de5150a4eea496a6947aed341d8aad29d07f` | `VERIFIED` |
| **Narration Provenance** | $\text{Canonical} == \text{TTS} == \text{Subtitles} == \text{Render}$ | `26bf08d3...` across all 4 boundaries | `VERIFIED` |
| **TTS Engine & Voice** | EdgeTTS / `en-US-GuyNeural` | `edge-tts` (`en-US-GuyNeural`, Rate: `+0%`, Pitch: `+0Hz`) | `REAL` |
| **Synthesized Audio Duration** | Spoken narration duration | `49.78s` (Audio SHA: `a7d01ec8...`) | `VERIFIED` |
| **Subtitles** | Synchronized SRT cues | 24 cues (SHA: `fc22155b...`) | `VERIFIED` |
| **Rendered Video Resolution** | $1080 \times 1920$ (9:16 Vertical) | $1080 \times 1920$ | `PASS` |
| **Video Frame Rate** | 30.0 fps constant ($\pm 0.5\text{ fps}$) | 30.0 fps | `PASS` |
| **Video / Audio Codecs** | H.264 / AAC | `h264` (`yuv420p`) / `aac` stereo | `PASS` |
| **Final Video Duration** | Equal to audio duration ($\pm 0.5\text{s}$) | Video: `49.77s`, Audio: `49.78s` (Drift: `0.009s`) | `PASS` |
| **Final Master Loudness** | Target: -14.0 LUFS ($\pm 1.5\text{ LU}$, $[-15.5, -12.5]$ LUFS) | **-14.96 LUFS** | `PASS` |
| **Rendered MP4 File Size** | Valid non-empty media file | `3,279,204 bytes` (~3.28 MB) | `PASS` |
| **Rendered MP4 SHA-256** | Final video content hash | `415b69a60054d875cecb5917662f945af9e880177f4cf4a64499f15a7c753623` | `AUDITABLE` |
| **Production Fingerprint** | Unique combination digest | `a5baab22237691bc74b83d4c281503c736120303adfe6a7843fa30934bfa2e4c` | `AUDITABLE` |
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
================ 115 passed, 2 deselected in 72.61s (0:01:12) =================
```
- **Database & Migrations**: 13/13 PASSED
- **Domain Models & Lifecycle State Machine**: 15/15 PASSED
- **Research, Topic Strategy & Grounding**: 30/30 PASSED
- **Media Pipeline & Contracts (`tests/media/`)**: 41/41 PASSED
  - `test_canonical_binding.py`: 4/4 PASSED
  - `test_capabilities.py`: 2/2 PASSED
  - `test_ffmpeg_renderer.py`: 3/3 PASSED
  - `test_media_pipeline.py`: 11/11 PASSED (including 5 cache invalidation & 2 capability blocker tests)
  - `test_media_qa.py`: 14/14 PASSED (including loudness parse error, loudness out-of-bounds, wrong video codec, wrong audio codec, wrong pixel format, wrong FPS, missing video/audio stream tests)
  - `test_scene_planning.py`: 2/2 PASSED
  - `test_subtitles.py`: 2/2 PASSED
  - `test_tts.py`: 3/3 PASSED
- **Smoke Tests**: 4/4 PASSED

### Live Test Gate (`pytest -v tests/media/test_tts_live.py`)
```bash
$ pytest -v tests/media/test_tts_live.py
============================== 1 passed in 3.04s ==============================
```

---

## 5. Phase 5 Sign-Off & Invariants

- [x] Spoken narration is bound exclusively to `script.get_canonical_narration()` with 100% hash parity.
- [x] Real EdgeTTS audio synthesis with rate and pitch controls.
- [x] Responsive 9:16 visual scene cards with Pillow and dynamic typography.
- [x] Per-scene normalized encoding and FFmpeg concat demuxer composition.
- [x] Subtitles burned with styled ASS / SRT format.
- [x] Measured final master loudness at -14.96 LUFS via EBU R128 (target -14.0 $\pm 1.5$ LU).
- [x] Fail-closed loudness parsing and strict technical QA gate assertions.
- [x] Idempotency enforced via mandatory `production_fingerprint` match prior to reuse.
- [x] All database records (`Asset`, `QualityResult`, `VideoProject`) persisted.
- [x] State transitions strictly enforce `VERIFIED -> PRODUCING -> RENDERED -> READY_FOR_REVIEW`.
- [x] Sanitized commit-safe manifest generated at `docs/evaluation/phase5_manifest.json`.
- [x] **STOPPED before Phase 6** (no YouTube OAuth/upload/scheduling/analytics).
