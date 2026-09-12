"""Unit tests for HookTournamentService and grounded hook strategy."""

import pytest

from app.domain.enums import ContentFormat, HookAngle, PrimaryVideoGoal, TonePreset
from app.domain.models import (
    Claim,
    FactCheckReport,
    HookCandidate,
    ResearchDossier,
    ResearchSource,
    VideoCreativeBrief,
    resolve_default_creative_brief,
)
from app.services.hook_strategy import HookTournamentError, HookTournamentService


@pytest.fixture
def mock_dossier():
    return ResearchDossier(
        id="dos_wal_123",
        topic_id="top_wal_123",
        topic="SQLite WAL Mode Concurrency",
        summary="Technical analysis of SQLite Write-Ahead Logging concurrency mechanism.",
        queries=["sqlite concurrency wal mode"],
        sources=[
            ResearchSource(
                id="src_sqlite_wal",
                title="SQLite WAL Mode Documentation",
                url="https://sqlite.org/wal.html",
                content_snapshot="In WAL mode, readers do not block writers and a writer does not block readers. WAL significantly improves concurrency.",
                content_sha256="sha256_mock_wal_doc_content",
            )
        ],
        claims=[
            Claim(
                id="clm_wal_concurrency",
                statement="WAL mode allows readers to access the database while a write is occurring.",
                verified=True,
                source_id="src_sqlite_wal",
            )
        ],
    )


@pytest.fixture
def mock_brief():
    return resolve_default_creative_brief(
        content_format=ContentFormat.EXPLAINER,
        requested_duration=40.0,
    )


def test_hook_tournament_generates_distinct_angles(mock_dossier, mock_brief):
    """Tournament generation produces candidates across distinct psychological angles."""
    service = HookTournamentService()
    candidates = service.generate_hook_candidates(
        topic="SQLite WAL Concurrency",
        dossier=mock_dossier,
        brief=mock_brief,
        content_format=ContentFormat.EXPLAINER,
        candidate_count=5,
    )

    assert len(candidates) == 5
    angles = {c.angle for c in candidates}
    assert len(angles) == 5
    assert HookAngle.CURIOSITY_GAP in angles
    assert HookAngle.PAIN_POINT in angles
    assert HookAngle.CONTRARIAN in angles
    assert HookAngle.RESULT_FIRST in angles
    assert HookAngle.STAKES_FIRST in angles

    for c in candidates:
        assert c.text.strip() != ""
        assert c.promise.strip() != ""


def test_generic_intro_hook_is_penalized(mock_dossier, mock_brief):
    """Generic openers such as 'In this video' or 'Welcome back' receive severe penalty deductions."""
    service = HookTournamentService()

    generic_candidate = HookCandidate(
        text="In this video, welcome back, today we will explore how SQLite WAL mode works.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Learn about WAL mode in SQLite.",
    )
    strong_candidate = HookCandidate(
        text="There's a subtle lock in SQLite that silently freezes concurrent readers.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Expose the hidden locking behavior in SQLite.",
    )

    eval_generic = service.evaluate_hook(generic_candidate, 0, "SQLite WAL", mock_dossier, mock_brief)
    eval_strong = service.evaluate_hook(strong_candidate, 1, "SQLite WAL", mock_dossier, mock_brief)

    assert "GENERIC_OPENER" in eval_generic.penalties
    assert eval_generic.total_score < eval_strong.total_score
    assert eval_generic.total_score < 0.60


def test_unverified_numeric_hook_is_rejected(mock_dossier, mock_brief):
    """Hooks asserting ungrounded empirical metrics (e.g. 97% faster) are flagged unsafe and rejected."""
    service = HookTournamentService()

    fake_metric_candidate = HookCandidate(
        text="This one line change makes SQLite 97% faster in high-throughput benchmarks.",
        angle=HookAngle.RESULT_FIRST,
        promise="Show how to make SQLite 97% faster.",
    )

    eval_result = service.evaluate_hook(fake_metric_candidate, 0, "SQLite WAL", mock_dossier, mock_brief)

    assert eval_result.factual_safe is False
    assert any("UNVERIFIED_NUMERIC_CLAIM" in p for p in eval_result.penalties)
    assert eval_result.total_score == -100.0


def test_verified_numeric_hook_passes(mock_dossier, mock_brief):
    """Hooks asserting numbers grounded in dossier sources or claims remain factually safe."""
    # Add verified claim with metric
    mock_dossier.sources[0].content_snapshot += " Benchmark throughput reached 4000 transactions per second."
    service = HookTournamentService()

    grounded_metric_candidate = HookCandidate(
        text="WAL mode handles 4000 transactions per second without deadlocking readers.",
        angle=HookAngle.RESULT_FIRST,
        promise="Explain how SQLite achieves 4000 transactions per second.",
    )

    eval_result = service.evaluate_hook(grounded_metric_candidate, 0, "SQLite WAL", mock_dossier, mock_brief)

    assert eval_result.factual_safe is True
    assert eval_result.total_score > 0.60


def test_tournament_selects_safest_highest_scoring_hook(mock_dossier, mock_brief):
    """Tournament filters out unsafe candidates and picks the highest scoring safe hook."""
    service = HookTournamentService()

    candidates = [
        HookCandidate(
            text="In this video we'll explore SQLite WAL mode.",
            angle=HookAngle.CURIOSITY_GAP,
            promise="Explain WAL mode.",
        ),
        HookCandidate(
            text="This secret flag boosted throughput by 99% according to our tests.",
            angle=HookAngle.RESULT_FIRST,
            promise="Speed up database by 99%.",
        ),
        HookCandidate(
            text="If your background workers are crashing, SQLite WAL might be locking the database.",
            angle=HookAngle.PAIN_POINT,
            promise="Identify and resolve reader-writer locks in SQLite.",
        ),
    ]

    winner, winner_eval, all_evals = service.run_tournament(
        candidates=candidates,
        topic="SQLite WAL",
        dossier=mock_dossier,
        brief=mock_brief,
    )

    # Winner must be candidate index 2 (pain point), NOT index 1 (unsafe 99%) or index 0 (generic opener)
    assert winner.angle == HookAngle.PAIN_POINT
    assert winner_eval.hook_index == 2
    assert winner_eval.factual_safe is True
    assert all_evals[1].factual_safe is False


def test_tournament_raises_if_all_candidates_unsafe(mock_dossier, mock_brief):
    """If all candidates contain fabricated metrics, tournament raises HookTournamentError."""
    service = HookTournamentService()

    candidates = [
        HookCandidate(
            text="Boost read throughput by 88% with this trick.",
            angle=HookAngle.RESULT_FIRST,
            promise="Achieve 88% boost.",
        ),
        HookCandidate(
            text="Save $500000 on database licensing costs.",
            angle=HookAngle.STAKES_FIRST,
            promise="Save 500k.",
        ),
    ]

    with pytest.raises(HookTournamentError) as exc_info:
        service.run_tournament(candidates, "SQLite WAL", mock_dossier, mock_brief)

    assert "factual safety" in str(exc_info.value).lower()
