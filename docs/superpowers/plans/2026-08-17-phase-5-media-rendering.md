# Phase 5 — Real Media Factory, TTS, Subtitles, FFmpeg Rendering & Technical QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform verified script and canonical narration into real audio, subtitles, 1080x1920 scene cards, and rendered MP4 video with real FFprobe technical QA and EBU R128 loudness verification, advancing lifecycle from `VERIFIED` to `READY_FOR_REVIEW`.

**Architecture:** Deterministic Python pipeline driving EdgeTTS synthesis, proportional subtitle generator, Pillow scene card factory, FFmpeg concat & filtergraph compositor, and FFprobe/EBU R128 QA inspector.

**Tech Stack:** Python 3.12, EdgeTTS, Pillow, FFmpeg / FFprobe, SQLite (WAL mode), Pydantic v2, pytest.

## Global Constraints
- Phase 5 consumes only `VERIFIED` projects.
- Spoken narration is strictly immutable: `script.get_canonical_narration()` is the sole authoritative text.
- No LLM generation or rewriting during media production.
- Real FFprobe stream inspection and FFmpeg loudnorm loudness measurement (no fake or hardcoded metrics).
- Strict state transitions: `VERIFIED -> PRODUCING -> RENDERED -> READY_FOR_REVIEW` (or `QA_FAILED`).
- No YouTube OAuth/upload/scheduling/analytics (Phase 6 boundary).

---

### Task 1: Capabilities & Domain Models
**Files:**
- Modify: `app/domain/enums.py`
- Modify: `app/db/repository.py`
- Modify: `app/api/routes.py`
- Create: `app/media/capabilities.py`
- Create: `app/media/models.py`
- Test: `tests/media/test_capabilities.py`

### Task 2: Real TTS Backend & Canonical Narration Binding
**Files:**
- Create: `app/media/tts/base.py`
- Create: `app/media/tts/edge_tts_backend.py`
- Test: `tests/media/test_tts.py`
- Test: `tests/media/test_canonical_binding.py`

### Task 3: Subtitle Generation & Monotonicity
**Files:**
- Create: `app/media/subtitles.py`
- Test: `tests/media/test_subtitles.py`

### Task 4: Scene Visual Card Factory & Planner
**Files:**
- Create: `app/media/visual_factory.py`
- Create: `app/media/scene_planner.py`
- Test: `tests/media/test_scene_planning.py`

### Task 5: FFmpeg Composition Renderer & Technical QA
**Files:**
- Create: `app/media/ffmpeg_renderer.py`
- Create: `app/media/qa.py`
- Test: `tests/media/test_ffmpeg_renderer.py`
- Test: `tests/media/test_media_qa.py`

### Task 6: Media Pipeline & State Machine Integration
**Files:**
- Create: `app/media/pipeline.py`
- Test: `tests/media/test_media_pipeline.py`

### Task 7: REAL Phase 5 Runner & Verification Manifest
**Files:**
- Create: `scripts/run_real_phase5.py`
- Output: `docs/evaluation/phase5_manifest.json`
- Output: `docs/phase-5-verification.md`
