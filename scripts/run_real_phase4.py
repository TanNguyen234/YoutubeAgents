"""Real Phase 4 Integration Runner.

Executes Stages 1-5 across 3 distinct real-world topics using:
1. Real HTTP fetching (httpx)
2. Real SHA-256 calculation on downloaded bytes
3. Real Antigravity CLI reasoning (`agy --print ... --json-schema ...`)
4. Real claim extraction from final script voiceover
5. Real fact checking against downloaded source text
6. Verification gate decision
7. Evidence and artifact persistence in `output/phase4_evidence/` and SQLite
"""

import json
import sys
import time
from pathlib import Path

from app.core.backend import AntigravityCLIBackend
from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import VideoLifecycleState
from app.domain.models import Channel
from app.services.pipeline_brain import BrainPipeline
from app.services.research_agent import ResearchAgent, ResearchFetchError


TOPICS_CONFIG = [
    {
        "channel": Channel(
            id="chan-sqlite-real",
            title="Database Internals Hub",
            handle="@DatabaseInternals",
            niche="Storage Engines & Database Systems",
            target_audience="Backend software engineers and systems programmers",
        ),
        "keyword": "Mastering SQLite WAL Mode Concurrency",
        "seed_urls": ["https://sqlite.org/wal.html"],
        "expected_state": VideoLifecycleState.VERIFIED,
    },
    {
        "channel": Channel(
            id="chan-python-real",
            title="Python Architecture Weekly",
            handle="@PythonArch",
            niche="Advanced Python & Async Architectures",
            target_audience="Senior Python developers and software architects",
        ),
        "keyword": "Why Asyncio Uses Cooperative Multitasking",
        "seed_urls": ["https://docs.python.org/3/library/asyncio.html"],
        "expected_state": VideoLifecycleState.VERIFIED,
    },
    {
        "channel": Channel(
            id="chan-agents-real",
            title="Autonomous Agents Engineering",
            handle="@AutonomousAgents",
            niche="AI Agents & Control Plane Architecture",
            target_audience="AI engineers and technical founders",
        ),
        "keyword": "Building Bulletproof AI Agents with Antigravity",
        "seed_urls": ["https://raw.githubusercontent.com/TanNguyen234/YoutubeAgents/main/README.md"],
        "expected_state": VideoLifecycleState.VERIFIED,
    },
]


def run_real_phase4():
    print("=" * 80)
    print("Starting REAL Phase 4 Autonomous Intelligence & Grounding Execution")
    print("Backend: Antigravity CLI (agy)")
    print("Network: Live HTTP requests (no synthetic fallbacks)")
    print("=" * 80)

    db_path = Path("data/real_phase4.db")
    if db_path.exists():
        db_path.unlink()
    evidence_dir = Path("output/phase4_evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    init_database(db_path)
    repo = SQLiteRepository(db_path)
    backend = AntigravityCLIBackend(timeout_seconds=120)
    researcher = ResearchAgent(evidence_storage_dir=evidence_dir)

    pipeline = BrainPipeline(
        repository=repo,
        backend=backend,
        research_agent=researcher,
    )

    results = []

    for idx, item in enumerate(TOPICS_CONFIG):
        channel = item["channel"]
        keyword = item["keyword"]
        seed_urls = item["seed_urls"]
        project_id = f"proj-real-{idx+1:02d}"

        print(f"\n[{idx+1}/3] Processing Real Topic: '{keyword}'")
        print(f"    Channel: {channel.title} ({channel.niche})")
        print(f"    Seed URLs: {seed_urls}")

        repo.save_channel(channel)
        t0 = time.time()

        try:
            project, report = pipeline.run_stage_1_to_5(
                project_id=project_id,
                channel=channel,
                keyword=keyword,
                seed_urls=seed_urls,
                max_rewrite_attempts=2,
            )
            elapsed = time.time() - t0

            # Persist report and evidence in SQLite
            repo.save_fact_check_report(report)

            voiceover = (
                project.script.sections.voiceover_text
                if project.script and project.script.sections
                else " ".join(s.narration for s in project.script.scenes) if project.script else ""
            )

            print(f"    -> Final State: {project.state.value} (Elapsed: {elapsed:.2f}s)")
            print(f"    -> Fact Check Verdict: {report.overall_verdict.value} (Verified: {report.verified_count}, Failed: {report.failed_count})")
            if project.script:
                print(f"    -> Script Scenes: {len(project.script.scenes)}, Words: {project.script.total_word_count}, Est. Duration: {project.script.estimated_duration_seconds:.1f}s")
            print(f"    -> Voiceover Preview: {voiceover[:120]}...")

            evidence_summary = {
                "project_id": project.id,
                "channel_id": channel.id,
                "keyword": keyword,
                "state": project.state.value,
                "elapsed_seconds": round(elapsed, 2),
                "script": project.script.model_dump(mode="json") if project.script else None,
                "fact_check_report": report.model_dump(mode="json"),
            }

            artifact_path = evidence_dir / f"{project_id}_evidence.json"
            artifact_path.write_text(json.dumps(evidence_summary, indent=2), encoding="utf-8")
            print(f"    -> Saved Real Evidence Artifact: {artifact_path}")

            results.append({
                "topic": keyword,
                "state": project.state.value,
                "verdict": report.overall_verdict.value,
                "verified_count": report.verified_count,
                "failed_count": report.failed_count,
                "success": project.state == item["expected_state"],
            })

        except ResearchFetchError as e:
            elapsed = time.time() - t0
            print(f"    -> BLOCKED on Network Fetch: {e}")
            results.append({"topic": keyword, "state": "BLOCKED", "error": str(e), "success": False})
        except Exception as e:
            elapsed = time.time() - t0
            print(f"    -> FAILED with Error: {e}")
            results.append({"topic": keyword, "state": "FAILED", "error": str(e), "success": False})

    print("\n" + "=" * 80)
    print("REAL Phase 4 Execution Summary:")
    all_passed = True
    for r in results:
        status_str = "PASS" if r.get("success") else "FAIL/BLOCKED"
        print(f"  - [{status_str}] Topic: {r['topic']} -> State: {r['state']} (Verified: {r.get('verified_count', 0)}, Failed: {r.get('failed_count', 0)})")
        if not r.get("success"):
            all_passed = False
    print("=" * 80)

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    run_real_phase4()
