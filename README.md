# YouTube Autopilot

Autonomous video research, generation, rendering, publishing, and analytics orchestration system.

---

## 1. Pipeline Architecture

The YouTube Autopilot lifecycle consists of 15 connected phases:

```
Research
  ├──> Select Topic
  │     └──> Evidence
  │           └──> Script
  │                 └──> Fact Check
  │                       └──> Visual Plan
  │                             └──> Media Acquisition
  │                                   └──> TTS Voice Synthesis
  │                                         └──> Subtitle Generation
  │                                               └──> Video Rendering (FFmpeg)
  │                                                     └──> Automated QA
  │                                                           └──> Human Review Gate
  │                                                                 └──> YouTube Upload/Schedule
  │                                                                       └──> Analytics Tracking
  │                                                                             └──> Strategy Feedback
```

---

## 2. Core Architectural Tenets

- **Primary Control & Reasoning Plane**: Antigravity runtime, SDK, and specialized agents.
- **Reasoning Backend Policy**: No default external Gemini Developer API or third-party LLM endpoints for core reasoning tasks (research, topic selection, scripting, fact checking, QA, analytics).
- **Execution Hierarchy**:
  $$\text{Antigravity runtime / SDK} \longrightarrow \text{Antigravity agents} \longrightarrow \text{MCP} \longrightarrow \text{External APIs (only when strictly necessary)}$$
- **Mandatory YouTube Integrations**: YouTube Data API v3 (metadata, uploads, scheduling) and YouTube Analytics API.
- **Media Provenance & Copyright**: Absolute ban on raw reuploading of third-party content. Full asset provenance (URL, license, timestamp, hash) recorded for all external media.
- **Safety & Review**: All production video uploads default to `private`. Transition to public publication requires human operator sign-off.
- **Runtime State Discipline**: All runtime operations return typed states: `REAL`, `TEST`, `DRY_RUN`, `BLOCKED`, or `FAILED`. Silent failure masking is forbidden.
- **Simplicity First (Ponytail / YAGNI)**: Standard library first, SQLite default local persistence, FFmpeg primary video renderer.

---

## 3. Directory Layout

```
YoutubeAgents/
├── .agents/          # Agent operational rules and local skills
├── app/              # Core application logic and pipeline stages
├── config/           # Environment and runtime configurations
├── docs/             # Technical specifications, DoD, and architecture contracts
│   ├── project-contract.md
│   └── definition-of-done.md
├── tests/            # Test suite (smoke, unit, integration)
├── scripts/          # Automation and maintenance scripts
├── data/             # Local database and raw staging assets (gitignored)
├── output/           # Rendered media and build artifacts (gitignored)
├── references/       # Reference documents and guidelines
├── pyproject.toml    # Python project packaging & pytest configuration
├── .env.example      # Environment variables template
├── .gitignore        # Version control ignore rules
├── AGENTS.md         # Mandatory engineering contract for AI agents
└── README.md         # Project documentation
```

---

## 4. Development & Testing

### Running Tests
```bash
python -m pytest
```

### Checking Package Import
```bash
python -c "import app; import config; print(app.__version__)"
```

---

## 5. Phase Status

- **Phase 0**: Workspace Bootstrap & Engineering Contract — **COMPLETED**
- **Phase 1**: Reference Repositories Deep Audit & Architectural Decisions — **COMPLETED**
- **Phase 2**: Antigravity Runtime & Reasoning Plane — **NOT STARTED**
