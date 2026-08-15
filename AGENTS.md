# AGENTS.md — YouTube Autopilot Mandatory Engineering Rules

These rules apply to every coding agent, subagent, and workflow in the `YoutubeAgents` workspace. Current code, tests, and explicit verification output override historical plans.

---

## 1. Core Principles & Governance

1. **Core Language**: Python (>= 3.11) is the standard implementation language.
2. **Control & Reasoning Plane**: Antigravity is the primary control plane and reasoning environment.
3. **No Default External AI Reasoning**: Do **NOT** use Gemini Developer API (or generic external LLM endpoints) as the default reasoning backend for:
   - Research reasoning
   - Topic selection
   - Script generation
   - Fact checking
   - Metadata reasoning
   - QA reasoning
   - Analytics reasoning
4. **Execution Hierarchy**:
   $$\text{Antigravity runtime / SDK} \longrightarrow \text{Antigravity agents} \longrightarrow \text{MCP} \longrightarrow \text{External APIs (only when strictly necessary)}$$
5. **Mandatory YouTube Integrations**: YouTube Data API v3 and YouTube Analytics API are the official external APIs required for channel and video operations.
6. **Content Integrity & Copyright**: Absolutely **NO** reuploading or unlicensed mirroring of other creators' YouTube content. Originality, attribution, and transformation rules are strictly enforced.
7. **External Media Provenance**: Every external image, audio, video, or data asset must record verified provenance (source URL, license/usage rights, timestamp, SHA-256 hash).
8. **No Fake Integrations / No Production Mocks**: Production runtime code must use real implementations. Mocks belong solely in unit/integration test isolation and must be explicitly marked with `TEST`.
9. **No Fallback Masking**: Real operation failure must **never** be caught and silently replaced with a dummy placeholder to claim `SUCCESS`. If an operation fails, it must fail explicitly with `FAILED` or `BLOCKED`.
10. **Runtime State Rigor**: All runtime execution outcomes must strictly resolve to one of five typed states:
    - `REAL` (live production execution with verified external/internal effect)
    - `TEST` (isolated test execution using test doubles/fixtures)
    - `DRY_RUN` (simulated run verifying logic and schemas without remote mutation)
    - `BLOCKED` (execution paused waiting on missing prerequisites or human approval)
    - `FAILED` (explicitly failed step with error diagnostic)
11. **Human-in-the-Loop & Upload Safety**:
    - Production upload defaults to `private`.
    - Public / unlisted publication is strictly prohibited without explicit human review and approval.
12. **Simplicity & Anti-Bloat (Ponytail / YAGNI)**:
    - Standard library and native tooling first.
    - No speculative abstractions, one-use factories, or unrequested framework bloat.
    - Do not invent dependencies when stdlib or existing tools suffice.
13. **Data Persistence**: Local SQLite is the default database engine. PostgreSQL is an optional production backend only.
14. **Video Rendering**: FFmpeg is the primary rendering engine.

---

## 2. End-to-End Pipeline Contract

The YouTube Autopilot pipeline spans 15 linear and feedback-driven stages:

```
Research
  └──> Select Topic
        └──> Evidence
              └──> Script
                    └──> Fact Check
                          └──> Visual Plan
                                └──> Media
                                      └──> TTS
                                            └──> Subtitle
                                                  └──> Render
                                                        └──> QA
                                                              └──> Human Review
                                                                    └──> YouTube Upload/Schedule
                                                                          └──> Analytics
                                                                                └──> Strategy Feedback
```

---

## 3. Phase Transition & Quality Gates

Each project phase and feature increment must strictly conclude with:
- **Planner / Review Evidence**: Documented plan compliance and stable diff inspection.
- **Tests Executed**: Actual commands, execution logs, and pass/fail counts (TDD discipline: RED $\rightarrow$ GREEN).
- **Reviewer Result**: Explicit review of security, data flow, and error paths.
- **Verifier Verdict**: Formal verdict (`PASS` or `FAIL`).
- **Remaining Blockers**: Explicit listing of blockers or `NONE`.

> [!CAUTION]
> Under no circumstances may a subsequent phase begin if the verifier verdict is `FAIL` or if verification has not been executed.

---

## 4. Forbidden Tools in Bootstrap / Core Reasoning

- `gemini-notebook`
- `code-review-graph` (CRG)
- Headless browser automation (unless specifically required in a later dedicated media/research phase)
- External AI API wrappers used as default reasoning backends
- Unrequested heavyweight agent frameworks (`LangGraph`, `Deep Agents`)
